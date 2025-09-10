import os, sys; sys.path.append(os.path.dirname(os.path.realpath(__file__)))
import logging
from typing import Dict

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

def validate_response(func):
    def wrapper(self, response, *args, **kwargs):
        if response:
            func(self, response, *args, **kwargs)
        else:
            raise ValueError("Error in response")
    return wrapper


class Codes:
    class _2f:
        def __init__(self, information_codes:list, length_to_read_per_meter:Dict,
                        old_data:Dict = None, it_id: int = 0, ) -> None:
            #bytes to read per every meter in 2f, f.e. 24 is 4 bytes(mostly every meter is 4bytes but...)
            self.length_to_read_per_meter = length_to_read_per_meter

            #codes to read, mostly information_codes are length_to_read_per_meter keys
            self.information_codes = information_codes

            #old data for finding new values from responde
            self.old_data = old_data or dict.fromkeys(self.information_codes, '0')

            #it_id is used for database, to split every poll into blocks
            self.it_id = it_id

            #current game number for data
            self.game_number = 0
            
        def process_data(self, response):

            """find new values from machine's response, then yield it """
            log.info(f"it id is {self.it_id}")

            self.it_id += 1
            log.info(f"it id is {self.it_id}")
            log.info(response.data)
            self.game_number, cleaned_data = response.data[:2], \
                                        self.get_clean_data(response.data[2:]) # 2 is Game number
            log.info('clean_data')
            log.debug(cleaned_data) 
            for code in self.information_codes:
                if cleaned_data[code] != self.old_data[code] and cleaned_data[code] != 0:
                    value = int(cleaned_data[code]) - int(self.old_data[code])
                    log.debug(f'value is {value}')
                    yield code, value
                    self.old_data[code] = cleaned_data[code]

        def get_clean_data(self, raw_data: list) -> Dict[str, str]:
            """Split data into blocks of meters from 2f"""
            cleaned_data = {}
            pointer = iter(raw_data)
            while (meter := next(pointer, None)):
                cleaned_data[meter] = ''.join(
                    [
                        next(pointer)\
                        for _ in range(self.length_to_read_per_meter[meter])
                    ]
                    )
                """Get only needed information from data"""
            return cleaned_data  
        

