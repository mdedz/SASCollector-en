#!/usr/bin/python3.11
import sys
import logging
import os
import datetime
import threading
import time
import uuid
import socket
import traceback
import atexit
import subprocess
import asyncio
from typing import Any, Dict, List, Optional, Union, Generator, Tuple
from dotenv import load_dotenv

from app.modules.collector import SlotMachine, Response
from app.modules.network.connection_server import client
from app.modules.utils.codes import Codes
from app.modules.collector.credits import CreditSender
from app.modules.db import Database, read_json

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, filename="main.log", filemode="w",
                    format="%(asctime)s %(levelname)s %(message)s")


class Collector:
    """
    Main collector class for interacting with a slot machine, handling commands,
    sending jackpots, and persisting gaming machine data to the database.
    """
    jackpot_meter: Dict[str, Any]
    gaming_transactions_table: Optional[str]
    db: Database
    slot_machine: SlotMachine
    mac_address: str
    pc_name: str
    commands: 'Commands'
    listening: bool = True

    def __init__(
        self, 
        host: str, 
        user: str, 
        password: str, 
        database: str, 
        driver: str,
        com_port: str, 
        baudrate: int, 
        address: int, 
        wakeup_bit: int
    ) -> None:
        """
        Initialize collector with database connection and slot machine interface.
        
        Args:
            host (str): Database host.
            user (str): Database username.
            password (str): Database password.
            database (str): Database name.
            driver (str): Database driver.
            com_port (str): COM port for slot machine connection.
            baudrate (int): Baud rate for COM port.
            address (int): Address of the slot machine.
            wakeup_bit (int): Bit to wake the machine from idle state.
        """
        self.jackpot_meter = {
            "command": "8A",
            "poll_type": "S",
            "optional_data": []
        }
        self.gaming_transactions_table = os.getenv('table_name')

        self.db = Database(host, user, password, database, driver)
        self.slot_machine = SlotMachine(com_port, baudrate, address, wakeup_bit)
        self.mac_address = str(self.get_unique_id()) or ''
        self.pc_name = socket.gethostname() or ''
        
        self.commands = Commands(db=self.db, collector=self)
        
        self.check_current_gaming_machine()
        
        atexit.register(self.on_exit)

        self.add_listeners()
        self.add_do_once()

    def get_unique_id(self) -> str:
        """Return the unique machine ID from /var/lib/dbus/machine-id."""
        with open("/var/lib/dbus/machine-id") as f:
            return f.read().strip()

    def check_current_gaming_machine(self) -> None:
        """
        Check if the current PC and slot machine are registered in the database.
        If not, insert them.
        """
        gaming_machine: List[Dict[str, Any]] = self.db.query_string__select(
            f"SELECT * FROM GameMachines\
            where PC_name = '{self.pc_name}' and \
                serial_number = '{self.slot_machine.serial_number}' and mac = '{self.mac_address}'"
        )
        log.info(gaming_machine)
        log.info(self.pc_name)
        log.info(self.slot_machine.serial_number)
        log.info(self.mac_address)
        if not gaming_machine:
            self.db.insert(
                "GameMachines", 
                ["description", 'PC_name', 'serial_number', 'mac', "disable"], 
                ["nothing", self.pc_name, self.slot_machine.serial_number, self.mac_address, 0]
            )
 
    def jackpot(self, value: Union[int, float, str]) -> None:  
        """
        Send a jackpot amount to the slot machine.
        
        Args:
            value (int | float | str): Amount won in machine's currency.
        """
        log.info("jackpot called")
        [[exchange_currency]] = self.db.select('exchange_rate', ['exchange_currency'])
        log.info(exchange_currency)
        exchange_currency_value: float = float(exchange_currency)
        jackpot_value: str = str(int(float(value)/exchange_currency_value)).zfill(8)
        log.info(f"jackpot value {jackpot_value}") 
        
        self.send_jackpot(jackpot_value)

    def send_jackpot(self, jackpot_value: str) -> None:
        """
        Prepare and send jackpot meter command to the slot machine.
        
        Args:
            jackpot_value (str): Jackpot amount formatted as 8-digit string.
        """
        jackpot_meter: Dict[str, Any] = self.jackpot_meter.copy()
        jackpot_meter['optional_data'] = [jackpot_value[i:i+2] for i in range(0, len(jackpot_value), 2)]
        jackpot_meter['optional_data'].append('00')
        jackpot_meter['response_type'] = "ack_nack"
        
        log.info(f"jackpot meter {jackpot_meter}")
        self.slot_machine.add_one_task(**jackpot_meter)
            
    def __call__(self) -> None:
        """Continuously capture slot machine events and process them."""
        for response in self.slot_machine.capture_events():  # type: ignore
            if response:
                self.commands.get(response.command, BlankCommand).process_data(response)
    
    def add_listeners(self) -> None:
        """Initialize listeners for slot machine commands based on JSON config."""
        data: List[Dict[str, Any]] = read_json('tasks', 'listeners.json')
        for value in data:
            command: str = value['command']
            commit: bool = value.get('commit', False)

            kwargs: Dict[str, Any] = {'commit': commit}
            if command.lower() == '2f':
                [[it_id]] = self.db.query_string__select(
                    'SELECT TOP 1 it_id FROM gaming_transactions ORDER BY ID DESC;'
                ) or [[0]]
                kwargs['it_id'] = it_id
                kwargs['old_data'] = dict.fromkeys(list(value['length_to_read_per_meter'].keys()), '0')
                
            log.debug(f'command is {command} {kwargs}')

            if "length_to_read_per_meter" in value:
                self.commands.init_meter(
                    command, 
                    information_codes=list(value['length_to_read_per_meter'].keys()),
                    length_to_read_per_meter=value.pop("length_to_read_per_meter"),
                    **kwargs
                )
            else:
                self.commands.init_meter(command, **kwargs)
            
            if command.lower() == '2f':
                old_data: Dict[str, str] = self.get_prev_values_2f(value)
                self.commands['2f'].old_data = old_data

            log.info(value)
            self.slot_machine.add_listener(**value)

    def add_do_once(self) -> None:
        """Add tasks that should run only once on startup."""
        data: List[Dict[str, Any]] = read_json('tasks', 'do_once.json')
        log.debug("adding do once in main")
        for value in data:
            self.slot_machine.add_one_task(**value)

    def on_exit(self, *args: Any, **kwargs: Any) -> None:
        """Stop the collector when program exits."""
        self.listening = False

    def get_prev_values_2f(self, task: Dict[str, Any]) -> Dict[str, str]:
        """
        Get current state of a machine's counters for 2F command.
        
        Args:
            task (Dict[str, Any]): Task configuration dictionary.
        
        Returns:
            Dict[str, str]: Mapping of counter codes to their previous values.
        """
        log.debug(f'task {task}')
        response: Response = self.slot_machine.write_until_true(self.slot_machine.write)(
            **self.slot_machine.get_transformed_task(**task)
        )
        log.debug(f'response get prev 2f {response.error}')
        prev_codes: Dict[str, str] = {}
        for code, value in self.commands['2f'].raw_process_data(response):
            prev_codes[code] = value 

        log.debug(f'prev codes {prev_codes}')
        return prev_codes


class Commands(Codes):
    """
    Class for managing slot machine commands and their corresponding meters.
    """
    meters: Dict[str, Any] = {}

    def __init__(self, db: Database, collector: Collector) -> None:
        """
        Initialize Commands with database and collector reference.
        
        Args:
            db (Database): Database instance.
            collector (Collector): Collector instance.
        """
        self.db: Database = db
        self.collector: Collector = collector
        super().__init__()

    class _2f(Codes._2f):
        """
        Command 2F: Handles reading and saving counter values to database.
        """
        def __init__(self, db: Database, collector: Collector,
                     commit: bool = True, *args: Any, **kwargs: Any) -> None:
            """
            Initialize _2f command handler.
            
            Args:
                db (Database): Database instance.
                collector (Collector): Collector instance.
                commit (bool): Whether to commit data to database.
            """
            self.db: Database = db
            self.collector: Collector = collector
            self.commit: bool = commit
            super().__init__(*args, **kwargs)

        @property
        def raw_process_data(self) -> Any:
            """Return the raw process_data method from Codes._2f."""
            return super().process_data

        def process_data(self, response: Response) -> None:
            """
            Process and save counter data from slot machine to database.
            
            Args:
                response (Response): Response from slot machine.
            """
            log.debug('data processing in _2f')
            if not response or not self.commit:
                return
            for code, value in super().process_data(response):
                log.debug(f'data processing in _2f {code} and {value}')
                self.db.execute_with_check(self.db.insert)(
                    'gaming_transactions', 
                    (
                        'time_', 
                        'mac', 'property_code',
                        'value', 'game_number',
                        'it_id'
                    ),
                    (
                        datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-4],
                        self.collector.mac_address, code,
                        value, ''.join(self.game_number),
                        self.it_id
                    )
                )

    def init_meter(self, meter: str, **kwargs: Any) -> None:
        """
        Initialize a meter (command handler) with its parameters.
        
        Args:
            meter (str): Command code (e.g., '2F').
        """
        meter = meter.lower()
        q = getattr(self, '_' + meter, None)
        log.debug(f'kwargs {kwargs}')
        if q:
            self.meters[meter] = q(db=self.db, collector=self.collector, **kwargs)

    def __getitem__(self, meter: str) -> Any:
        """Return the initialized command meter by key."""
        meter = meter.lower()
        log.debug('getting data')
        return self.meters[meter]
    
    def get(self, key: str, def_val: Any) -> Any:
        """Return the command meter by key, or default if not found."""
        return self.meters.get(key.lower(), def_val)        
   
   
class BlankCommand:
    """Skip process data if there is no initiated command"""
    def process_data(self, *args: Any, **kwargs: Any) -> None:
        """Do nothing if command is not initialized."""
        pass
    

if __name__ == '__main__':
    load_dotenv()
    
    host: str = os.getenv('host')
    user: str = os.getenv('user')
    password: str = os.getenv('password')
    database: str = os.getenv('database')
    
    settings: Dict[str, Any] = read_json('constants', 'settings.json')
    
    driver: str = settings['db_driver'] 
    com_port: str = settings['com_port']
    baudrate: int = int(settings['baudrate'])
    address: int = int(settings['address'])
    wakeup_bit: int = int(settings['wakeup_bit'])
    try:
        collector: Collector = Collector(host, user, password, database, driver,
                                         com_port, baudrate, address, wakeup_bit)
        asyncio.run(client(collector))
        collector()
    except Exception as e:
        log.critical(e, exc_info=True)
