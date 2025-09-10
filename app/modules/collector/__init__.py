import time
import datetime
from typing import Generator, List
import serial
from app.modules.crc import CRC16Kermit as Kermit
import atexit
import logging
log = logging.getLogger(__name__)


READ_ITERATIONS = 5
READ_DELAY = .1
READ_SIZE_BYTES = 1
CRC_LENGTH = 2
BCD_GAME_NUMBER_LENGTH = 2


class WrongCRC(Exception):
    def __init__(self, data: List) -> None:
        super().__init__()
        self.message = """Exception is raised when the crc from machine did not match the hand-calculated crc.\n
                            That's is really better to try send your data again.\n
                            The response is\n""" + ' '.join(map(str, data)) 


class IterationsExceeded(Exception):
    """The max amout of iterations exceeded"""


class Response:
    """The Response from Slot Machine"""
    def __init__(self, data: List[str] = [], poll_type: str = [], error: bool = False,
                 ack_nack: hex = None, command: int = 0) -> None:
        self.error = error
        self.raw_data = data
        self.poll_type = poll_type
        self.ack_nack = ack_nack
        self.length_to_read = None
        self.command = command
        if not self.error and not ack_nack:
            self.address, self.command, *self.data = data[:-CRC_LENGTH] 
            self.crc = data[-CRC_LENGTH:]
            if poll_type == 'M':
                self.length_to_read, self.data = self.data[0], self.data[1:]
        else:
            self.raw_data = ['Error']

    def __str__(self) -> str:
        return ' '.join(map(str, self.raw_data))

    def __iter__(self) -> iter:
        return iter(self.raw_data)

    def __eq__(self, object) -> bool:
        return self.raw_data == object

    def __len__(self) -> int:
        return len(self.raw_data)

    def __bool__(self) -> bool:
        return not self.error


kermit = Kermit().calculate

def transform(data: List[int]) -> bytes:
    """Transforms data from list of integers to bytes"""
    return bytes.fromhex(''.join(map(lambda x: hex(x)[2:].zfill(2), data)))

def crc_calculate(command: List[int]) -> List[int]:
    """Calculates CRC16Kermit from list of integers"""
    transformed_command = transform(command[1:]) # slice is used, because CRC calculates without wakeup bit set!
    hex_crc = hex(kermit(transformed_command))[2:] # no need in 0x
    crc_string = hex_crc.zfill(4) # because we use 16 bit CRC
    crc_sliced = [int(crc_string[:CRC_LENGTH], 16), int(crc_string[CRC_LENGTH:], 16)]# crc as a list
    return crc_sliced

def transform_optional_data(data):
    return list(map(lambda x: int(x, 16), data)) if data else []

def transform_command(command):
    return int(command, 16)


class SlotMachine:
    """Main class to communicate with Slot Machine"""

    def __init__(
        self, 
        com_port: str, 
        baudrate: int=19200, 
        address: int=1, 
        wakeup_bit: int=128
    ) -> None:
        self.com_port = com_port
        self.baudrate = baudrate
        self.address = address
        self.wakeup_bit = wakeup_bit
        
        self.ack = self.address
        self.nack = hex(0x80 | self.address)
        
        self.string_address = self.address.to_bytes(1, byteorder='big')
        self.wakeup_bit_and_address = [self.wakeup_bit, self.address]
        self.listeners_tasks = []
        self.single_shots_tasks = []
        
        self.port = serial.Serial(self.com_port, baudrate=self.baudrate, 
                                  parity=serial.PARITY_EVEN, timeout=1)

        self.serial_number = self.get_serial_number() or ''
        
        atexit.register(self.on_exit)

    def get_transformed_task(self, command: int, optional_data: List[str]=None, **kwargs):
        return {
            'command' : transform_command(command), 
            'optional_data' : transform_optional_data(optional_data), 
            **kwargs
            }

    def add_listener(self, command: int, optional_data: List[str]=None, **kwargs):
        """
        Adds task to listen.
        """
        self.listeners_tasks.append(self.get_transformed_task(command, optional_data, **kwargs))

    def add_one_task(self, command: int, optional_data: List[str]=None, **kwargs):
        """
        Adds task to listen.
        """
        self.single_shots_tasks.append(self.get_transformed_task(command, optional_data, **kwargs))
        
    def capture_events(self) -> Generator[Response, None, None]:
        """
        Interrogates the client.
        """
        while 1:
            time.sleep(.5)
            tasks_log = {}
            log.info("single shots task")
            for task in self.single_shots_tasks:
                data = self.write(**task)
                print(data)
                yield data
                log.debug(self.single_shots_tasks)

            for _id, task in enumerate(self.listeners_tasks):
                log.debug('poll')      

                _t = task.pop('time') if 'time' in task else 0
                if (datetime.datetime.now().now().second - (tasks_log[_id].second if _id in tasks_log else _t*(-1))) >= _t:
                    tasks_log[_id] = datetime.datetime.now()
                    data = self.write(**task)

                yield data
                
            self.single_shots_tasks = []
                
    def write_type_R(self, command: int, length_to_read: int = 0, following_length: bool = False):
        return self.write(command, length_to_read=length_to_read, 
                           following_length=following_length, poll_type="R")

    def write_type_S(self, command: int, optional_data: List[int] = [], following_length: bool = False,
                     length_to_read: int = 0, add_length_binary: bool = False):
        return self.write(command, optional_data=optional_data, poll_type="S", following_length = following_length,
                           length_to_read=length_to_read, add_length_binary = add_length_binary)
    
    def write_type_M(self, command: int,  optional_data: List[int] = [], 
                     BCD_game_number: List[int] = [0, 0], 
                     following_length: bool = True, length_to_read: int = 0,
                     add_length_binary: bool = False):
        return self.write(command, optional_data=optional_data, BCD_game_number=BCD_game_number,
                           poll_type="M", following_length = following_length, length_to_read=length_to_read,
                           add_length_binary = add_length_binary)

    def write_until_true(self, func):
        def wrapper(*args, **kwargs):
            while True:
                r = func(*args, **kwargs)
                if r:
                    return r
                time.sleep(1)
        return wrapper
        
    def write(self, command: int, optional_data: List[int]=[], 
               length_to_read: int=0, poll_type: str = "", 
               BCD_game_number: List[int] = [0, 0], following_length: bool = False,
               add_length_binary: bool = False, response_type: str = 'normal', *args, **kwargs) -> Response:
        """
        following_length is used in response
        add_length_binary is used in command
        Writes data to the client.
        length to read does not include the crc in output.
        """
        log.debug('command {} and optdata {}'.format(command, optional_data))
        raw_command = self.wakeup_bit_and_address.copy()

        if poll_type == "R":
            raw_command.append(command)

        elif poll_type in ("M", "S"):
            raw_command.append(command)
            if add_length_binary:
                raw_command.append((len(BCD_game_number) if poll_type == "M" else 0) + len(optional_data))
            if poll_type == "M":
                raw_command.extend(BCD_game_number)

            raw_command.extend(optional_data)
            raw_command.extend(crc_calculate(raw_command))
        log.debug('raw command {}'.format(raw_command))
        print([hex(x) for x in raw_command])
        data = transform(raw_command)
        log.debug('data {}'.format(data))

        self.port.write(data)
        return self.read(bytes.fromhex(hex(command)[2:].zfill(2)), poll_type = poll_type, 
                         length_to_read = length_to_read, following_length = following_length,
                         response_type = response_type)

    def read(self, command: bytes, poll_type: str='', length_to_read: int = 0,
             following_length: bool = False, response_type: str = 'normal') -> Response:
        """Reads port.\n
        poll_type can be R, S, M, G (See docs)
        """
        log.debug('reads data {} {} {} {} {}'.format(command, poll_type, length_to_read, following_length, response_type))
        if response_type == "ack_nack":
            ack_nack = self.port.read(READ_SIZE_BYTES)
            log.debug(ack_nack)
            return Response(ack_nack = ack_nack, poll_type=poll_type, command=command)
            
        for _ in range(READ_ITERATIONS):
            if (t_d := self.port.read(READ_SIZE_BYTES)) == command:
                log.debug("Reading data {}".format(t_d))

                text = self.string_address + command 
                log.debug('following_length = {}'.format(following_length))
                if following_length:
                    length_bytes = self.port.read(READ_SIZE_BYTES)
                    length_to_read = int.from_bytes(length_bytes, byteorder='little')
                    log.debug('length to read = {}'.format(length_to_read))
                    text += length_bytes

                for _ in range(length_to_read + CRC_LENGTH): # plus crc
                    p_v_c = self.port.read(READ_SIZE_BYTES)
                    text += p_v_c
                    log.debug("Reading data {}".format(p_v_c))

                data = bytes.hex(text, " ").split(' ')
                log.debug('read data = {}'.format(data))

                self.validate_crc(text)
                __R = Response(data, poll_type)
                print("read data: ", __R)
                return __R
            
            log.debug("Reading data {}".format(t_d))
            time.sleep(READ_DELAY)
        return Response(error=True, command=command)
    
    def validate_crc(self, data: List[bytes]):
        crc = kermit(data)
        if crc != 0:
            log.debug('the crc is wrong')
            raise WrongCRC(data)

    def get_serial_number(self):
        response = self.write_type_R(0x54, following_length=True)
        
        if response:
            serial_number = ''.join(response.data[3:])
            return serial_number
        
    def add_credits(self, credits: List[int]):
        return self.write_type_S(0x9A, following_length=False, length_to_read=14)
    
    def on_exit(self):
        self.port.close()