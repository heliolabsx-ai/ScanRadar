"""
Builds the F&O stock universe from Kite's instrument dump:
every NFO stock future with the nearest expiry -> (equity token, futures token).
Indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) are excluded.
"""
import datetime as dt
import logging
import pandas as pd

log = logging.getLogger("universe")
INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}

# Sector map — extend freely; unmapped names fall under OTHERS.
SECTOR = {
  **{s: "IT" for s in ["WIPRO","TECHM","INFY","TCS","HCLTECH","LTIM","PERSISTENT","COFORGE","MPHASIS","KPITTECH","TATAELXSI","LTTS","OFSS","CYIENT","SONACOMS"]},
  **{s: "BANK" for s in ["SBIN","HDFCBANK","ICICIBANK","AXISBANK","KOTAKBANK","INDUSINDBK","FEDERALBNK","BANDHANBNK","IDFCFIRSTB","AUBANK","RBLBANK","YESBANK","PNB","BANKBARODA","CANBK","UNIONBANK","INDIANB","BANKINDIA"]},
  **{s: "NBFC" for s in ["BAJFINANCE","BAJAJFINSV","CHOLAFIN","SHRIRAMFIN","MUTHOOTFIN","MANAPPURAM","LICHSGFIN","PNBHOUSING","POONAWALLA","SBICARD","PEL","ABCAPITAL","LTF","JIOFIN","IIFL"]},
  **{s: "PSU" for s in ["IRFC","RVNL","PFC","RECLTD","BHEL","NHPC","SJVN","IRCTC","HAL","BEL","BDL","MAZDOCK","COCHINSHIP","NBCC","HUDCO","IREDA","RAILTEL"]},
  **{s: "PHARMA" for s in ["DIVISLAB","SUNPHARMA","CIPLA","DRREDDY","LAURUSLABS","MANKIND","LUPIN","AUROPHARMA","ALKEM","TORNTPHARM","ZYDUSLIFE","GLENMARK","BIOCON","GRANULES","IPCALAB","ABBOTINDIA","APOLLOHOSP","MAXHEALTH","FORTIS","SYNGENE"]},
  **{s: "AUTO" for s in ["M&M","TATAMOTORS","MARUTI","TVSMOTOR","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT","ASHOKLEY","MOTHERSON","BOSCHLTD","BHARATFORG","EXIDEIND","TIINDIA","UNOMINDA"]},
  **{s: "METAL" for s in ["JSWSTEEL","TATASTEEL","HINDALCO","NMDC","VEDL","SAIL","JINDALSTEL","NATIONALUM","HINDCOPPER","COALINDIA","APLAPOLLO","JSL"]},
  **{s: "OIL&GAS" for s in ["RELIANCE","ONGC","BPCL","IOC","HINDPETRO","GAIL","PETRONET","OIL","IGL","MGL","ATGL","GUJGASLTD"]},
  **{s: "CAP MKTS" for s in ["ANGELONE","360ONE","BSE","MCX","HDFCAMC","CDSL","CAMS","KFINTECH","NUVAMA","MOTILALOFS","IIFLCAPS"]},
  **{s: "CONSUMER" for s in ["DIXON","VOLTAS","HAVELLS","TITAN","TRENT","DMART","BLUESTARLTD","CROMPTON","KALYANKJIL","PAGEIND","ABFRL","NYKAA","ZOMATO","ETERNAL","JUBLFOOD","DEVYANI","INDHOTEL"]},
  **{s: "FMCG" for s in ["BRITANNIA","ITC","HINDUNILVR","NESTLEIND","DABUR","MARICO","GODREJCP","COLPAL","TATACONSUM","VBL","UNITDSPR","PATANJALI","EMAMILTD"]},
  **{s: "TELECOM" for s in ["BHARTIARTL","IDEA","INDUSTOWER","TATACOMM","HFCL"]},
  **{s: "CEMENT" for s in ["AMBUJACEM","ULTRACEMCO","SHREECEM","ACC","DALBHARAT","GRASIM","JKCEMENT"]},
  **{s: "POWER" for s in ["NTPC","POWERGRID","TATAPOWER","ADANIGREEN","ADANIENSOL","JSWENERGY","TORNTPOWER","CESC","SUZLON","INOXWIND"]},
  **{s: "CAP GOODS" for s in ["LT","SIEMENS","ABB","CGPOWER","CUMMINSIND","POLYCAB","KEI","HINDPETRO","THERMAX","KAYNES","BLUESTARLTD","ASTRAL","SUPREMEIND"]},
  **{s: "REALTY" for s in ["DLF","GODREJPROP","OBEROIRLTY","LODHA","PRESTIGE","PHOENIXLTD","BRIGADE"]},
  **{s: "CHEM" for s in ["PIIND","SRF","UPL","DEEPAKNTR","AARTIIND","NAVINFLUOR","ATUL","TATACHEM","CHAMBLFERT","COROMANDEL","SOLARINDS"]},
  **{s: "INSURANCE" for s in ["SBILIFE","HDFCLIFE","ICICIPRULI","ICICIGI","LICI","MAXFIN","NIACL","GICRE"]},
  **{s: "LOGISTICS" for s in ["DELHIVERY","CONCOR","ADANIPORTS","INDIGO","GMRAIRPORT","BLUEDART"]},
  **{s: "NEW-AGE" for s in ["PAYTM","POLICYBZR","NAUKRI","ZOMATO","SWIGGY","OLAELEC","FIRSTCRY"]},
  **{s: "DEFENCE" for s in ["HAL","BEL","BDL","MAZDOCK","SOLARINDS","DATAPATTNS"]},
}


def build(kite) -> pd.DataFrame:
    nfo = pd.DataFrame(kite.instruments("NFO"))
    nse = pd.DataFrame(kite.instruments("NSE"))
    fut = nfo[(nfo.instrument_type == "FUT") & (~nfo.name.isin(INDEX_NAMES))].copy()
    fut["expiry"] = pd.to_datetime(fut["expiry"]).dt.date
    today = dt.date.today()
    fut = fut[fut.expiry >= today]
    # nearest expiry per underlying
    fut = fut.sort_values("expiry").groupby("name", as_index=False).first()
    eq = nse[(nse.segment == "NSE") & (nse.instrument_type == "EQ")][["tradingsymbol", "instrument_token"]]
    eq = eq.rename(columns={"tradingsymbol": "name", "instrument_token": "eq_token"})
    u = fut.merge(eq, on="name", how="inner")
    u = u.rename(columns={"name": "symbol", "instrument_token": "fut_token", "tradingsymbol": "fut_symbol"})
    u["sector"] = u.symbol.map(SECTOR).fillna("OTHERS")
    u = u[["symbol", "eq_token", "fut_token", "fut_symbol", "expiry", "lot_size", "sector"]].reset_index(drop=True)
    log.info("universe: %d F&O stocks, nearest expiry %s", len(u), u.expiry.min())
    return u
