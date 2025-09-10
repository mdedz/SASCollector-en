from datetime import datetime
import logging

log = logging.getLogger(__name__)

class CreditSender:
    """
    Handles Automated Funds Transfer (AFT) credit operations with a SlotMachine.
    
    Responsibilities:
    - Validate credit transfer parameters
    - Convert amounts and data to SAS protocol BCD format
    - Build and send commands to the SlotMachine
    - Handle responses and interpret SAS status codes
    
    Example usage:
        sender = CreditSender(slot_machine)
        config = {
            'transfer_type': 'EGM',
            'cashable': 1000,
            'restricted': 0,
            'nonrestricted': 0,
            'asset_number': 12345,
            'partial_allowed': False,
            'receipt_request': True
        }
        result = sender.send_credits(config)
    """

    def __init__(self, slot_machine):
        """
        Initialize CreditSender with a SlotMachine instance.

        Args:
            slot_machine: An instance of SlotMachine used to communicate with the device.
        """
        self.slot_machine = slot_machine
        self.transfer_types = {
            'EGM': 0x00,        # In-house to gaming machine
            'TICKET': 0x20,     # In-house to ticket
            'BONUS_COIN': 0x10, # Bonus coin out
            'BONUS_JACKPOT': 0x11, # Bonus jackpot
            'DEBIT_EGM': 0x40,  # Debit to EGM
            'DEBIT_TICKET': 0x60, # Debit to ticket
            'HOST': 0x80,       # Transfer to host
            'WIN_HOST': 0x90    # Win amount to host
        }

    def _validate_transfer(self, config):
        """
        Validate transfer parameters against SAS protocol specifications.
        
        Checks include:
        - At least one amount is non-zero
        - Debit transfers require POS ID
        - Transaction ID length <= 20 characters
        
        Raises:
            ValueError: If validation fails.
        """
        if config['cashable'] + config['restricted'] + config['nonrestricted'] == 0:
            raise ValueError("At least one amount must be non-zero")
        
        if config['transfer_type'] in [0x40, 0x60] and not config.get('pos_id'):
            raise ValueError("DEBIT transfers require POS ID")
        
        if len(config.get('transaction_id', '')) > 20:
            raise ValueError("Transaction ID max 20 characters")

    def _create_transfer_command(self, config):
        """
        Build SAS command bytes for AFT transfer.
        
        Converts amounts, flags, asset number, registration keys, expiration, 
        and other parameters into a SAS-compatible command byte list.
        
        Args:
            config (dict): Transfer configuration with required fields.
        
        Returns:
            List[int]: Byte list ready to send to the SlotMachine.
        """
        cmd = [
            config['transfer_code'],    # 00=Full, 01=Partial
            0x00,                       # Transaction index (new)
            config['transfer_type']
        ]

        # Add amounts (5-byte BCD each)
        cmd.extend(self._amount_to_bcd(config['cashable']))
        cmd.extend(self._amount_to_bcd(config['restricted']))
        cmd.extend(self._amount_to_bcd(config['nonrestricted']))

        # Transfer flags (bitmask)
        flags = 0
        if config.get('receipt_request', False):
            flags |= 0x80
        if config.get('custom_ticket_data', False):
            flags |= 0x20
        cmd.append(flags)

        # Asset number (4 bytes)
        cmd.extend(config['asset_number'].to_bytes(4, 'big'))

        # Registration key (20 bytes) - zeros for non-debit
        cmd.extend(config.get('registration_key', bytes(20)))

        # Transaction ID
        txid = config.get('transaction_id', self._generate_txid())
        cmd.append(len(txid))
        cmd.extend(txid.encode('ascii'))

        # Expiration (MMDDYYYY or days format)
        cmd.extend(self._format_expiration(config.get('expiration')))

        # Pool ID (2 bytes)
        cmd.extend(config.get('pool_id', 0x0000).to_bytes(2, 'big'))

        # Receipt data (if any)
        cmd.extend(self._prepare_receipt_data(config.get('receipt_data')))

        # Lock timeout (2-byte BCD)
        cmd.extend(self._lock_timeout(config.get('lock_timeout')))

        return cmd

    def send_credits(self, config):
        """
        Send credits to the slot machine according to the specified configuration.

        Validates parameters, builds SAS command, sends it, and interprets the response.

        Args:
            config (dict): Configuration dictionary containing:
                - 'transfer_type': str, key from transfer_types
                - 'cashable': int, cents
                - 'restricted': int, cents
                - 'nonrestricted': int, cents
                - 'asset_number': int
                - 'partial_allowed': bool
                - 'receipt_request': bool
                - 'expiration': datetime or str ('days' for default)
                - 'pool_id': int
                - 'pos_id': int (for debit)
                - 'registration_key': bytes (for debit)

        Returns:
            dict: Result including status, amounts, transaction_id or error message.
        """
        try:
            # Validate and prepare
            config['transfer_type'] = self.transfer_types[config['transfer_type']]
            config['transfer_code'] = 0x01 if config.get('partial_allowed') else 0x00
            self._validate_transfer(config)
            
            # Build command
            command_bytes = self._create_transfer_command(config)
            
            # Send and handle response
            response = self.slot_machine.write(
                command=0x72,
                optional_data=command_bytes,
                poll_type='S'
            )
            
            return self._handle_response(response)
            
        except Exception as e:
            log.error(f"Credit transfer failed: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    # Helper methods -------------------------------------------------

    def _amount_to_bcd(self, amount):
        """
        Convert an integer amount (in cents) to a 5-byte BCD list.
        
        Args:
            amount (int): Amount in cents, max 9999999999
        
        Returns:
            List[int]: 5-byte BCD representation of the amount.
        """
        if amount < 0 or amount > 9999999999:
            raise ValueError("Amount out of range")
        return [int(str(amount).zfill(10)[i:i+2]) for i in range(0, 10, 2)]

    def _format_expiration(self, expiration):
        """
        Convert expiration to 4-byte BCD for SAS protocol.
        
        Args:
            expiration (datetime | str | None): Expiration date or 'days' special format
        
        Returns:
            List[int]: 4-byte BCD representation
        """
        if isinstance(expiration, datetime):
            return [
                expiration.month,
                expiration.day,
                expiration.year // 100,
                expiration.year % 100
            ]
        elif expiration == 'days':
            return [0x00, 0x00, 0x00, 0x00]  # Special case
        else:
            return [0x00, 0x00, 0x00, 0x00]  # Default

    def _generate_txid(self):
        """Generate a unique transaction ID using current timestamp."""
        return f"TX{datetime.now().strftime('%Y%m%d%H%M%S')}"

    def _prepare_receipt_data(self, receipt_data):
        """
        Prepare receipt data bytes according to SAS Table 8.3f.
        
        Args:
            receipt_data: Optional receipt information
        
        Returns:
            List[int]: Formatted receipt data bytes (empty in simplified example)
        """
        return []  # Simplified for example

    def _lock_timeout(self, seconds):
        """
        Convert lock timeout in seconds to 2-byte BCD format (hundredths of seconds).
        
        Args:
            seconds (int | float): Timeout in seconds
        
        Returns:
            List[int]: 2-byte BCD representation
        """
        hundredths = int(seconds * 100)
        return [(hundredths // 100) % 100, hundredths % 100]

    def _handle_response(self, response):
        """
        Interpret SAS response bytes and return structured information.
        
        Args:
            response: Response object from SlotMachine.write
        
        Returns:
            dict: Includes status, amounts (cashable, restricted, nonrestricted),
                  and transaction ID
        """
        status_codes = {
            0x00: 'Full success',
            0x01: 'Partial success',
            0x40: 'Pending',
            0x80: 'Cancelled',
            0x93: 'Asset mismatch',
            # Add all status codes from Table 8.3e
        }
        return {
            'status': status_codes.get(response.data[0], 'Unknown'),
            'amounts': {
                'cashable': self._bcd_to_int(response.data[2:7]),
                'restricted': self._bcd_to_int(response.data[7:12]),
                'nonrestricted': self._bcd_to_int(response.data[12:17])
            },
            'transaction_id': bytes(response.data[23:-10]).decode('ascii')
        }

    def _bcd_to_int(self, bcd_bytes):
        """
        Convert 5-byte BCD representation back to integer.
        
        Args:
            bcd_bytes (List[int]): 5-byte BCD list
        
        Returns:
            int: Integer value
        """
        return int(''.join(f"{b:02d}" for b in bcd_bytes))
