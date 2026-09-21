import csv
import io
import requests
import logging

logger = logging.getLogger(__name__)

VALID_EQUITY_SYMBOLS = set()
_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

def load_valid_scrips():
    """
    Downloads the official Dhan Scrip Master CSV on startup
    and populates a global set of valid NSE/BSE equity symbols.
    This is used by the regex engine to validate symbols, forcing
    fallback to AI for typos or invalid tickers.
    """
    global VALID_EQUITY_SYMBOLS
    try:
        logger.info("Downloading Dhan scrip master for regex validation...")
        response = requests.get(_SCRIP_MASTER_URL, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Failed to download scrip master: HTTP {response.status_code}")
            return
            
        csv_data = csv.DictReader(io.StringIO(response.text))
        
        for row in csv_data:
            exchange = row.get("SEM_EXM_EXCH_ID", "")
            inst_type = row.get("SEM_INSTRUMENT_NAME", "")
            symbol = row.get("SEM_TRADING_SYMBOL", "")
            
            if exchange in ("NSE", "BSE") and inst_type == "EQUITY" and symbol:
                clean_symbol = symbol.replace("-EQ", "").strip().upper()
                VALID_EQUITY_SYMBOLS.add(clean_symbol)
                
        logger.info(f"Loaded {len(VALID_EQUITY_SYMBOLS)} valid equity symbols for regex fallback.")
        
    except Exception as e:
        logger.error(f"Error loading scrip master: {e}")
