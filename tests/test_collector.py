import os
from app.modules.collector.credits import CreditSender
from app.main import Collector


host = os.getenv('host')
user = os.getenv('user')
password = os.getenv('password')
database = os.getenv('database')


driver = os.getenv['db_driver'] 
com_port = os.getenv['com_port']
baudrate = int(os.getenv['baudrate'])
address = int(os.getenv['address'])
wakeup_bit = int(os.getenv['wakeup_bit'])

collector = Collector(host, user, password, database, driver,
                    com_port, baudrate, address, wakeup_bit
                    )

sender = CreditSender(self.slot_machine)

# Example 1: Regular in-house transfer to EGM
response1 = sender.send_credits({
    'transfer_type': 'EGM',
    'cashable': 500,  # $5.00
    'restricted': 0,
    'nonrestricted': 0,
    'asset_number': 0x12345678,
    'partial_allowed': True,
    'receipt_request': True,
    'lock_timeout':"1"
})

# Example 2: Print ticket
response2 = sender.send_credits({
    'transfer_type': 'TICKET',
    'cashable': 1000,
    'expiration': '12312025',
    'pool_id': 0x0001,
    'asset_number': 0x12345678
})

# Example 3: Debit withdrawal
response3 = sender.send_credits({
    'transfer_type': 'DEBIT_EGM',
    'cashable': 2000,
    'pos_id': 0x11223344,
    'registration_key': bytes.fromhex('A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6'),
    'asset_number': 0x12345678
})

