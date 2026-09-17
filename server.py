from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests,time

app=FastAPI(title="Crypto.com Signal Bot V1")
API="https://api.crypto.com/exchange/v1"
DURATIONS=["1m","2m","3m","5m","10m","15m","30m","1h","2h","3h"]
cache={}

app.mount("/static",StaticFiles(directory="app/static"),name="static")

def get_candles(symbol,tf="1m",count=220):
    r=requests.get(API+"/public/get-candlestick",params={"instrument_name":symbol,"timeframe":tf,"count":count},timeout=12)
    r.raise_for_status(); j=r.json()
    if j.get("code")!=0: raise RuntimeError(j.get("message","Crypto.com API error"))
    d=j["result"]["data"]; d.sort(key=lambda x:x["t"]); return d

def ema(v,p):
    if len(v)<p:return None
    k=2/(p+1); x=sum(v[:p])/p
    for n in v[p:]: x=n*k+x*(1-k)
    return x

def rsi(v,p=14):
    if len(v)<p+1:return None
    ds=[b-a for a,b in zip(v[-p-1:-1],v[-p:])]
    g=sum(max(x,0) for x in ds)/p; l=sum(max(-x,0) for x in ds)/p
    return 100 if l==0 else 100-100/(1+g/l)

def analyze(symbol,duration):
    rows=get_candles(symbol); c=[float(x["c"]) for x in rows]
    e50,e200=ema(c,50),ema(c,200); rr=rsi(c)
    if e50 is None or e200 is None or rr is None: raise RuntimeError("Not enough candle data")
    mom=(c[-1]/c[-6]-1)*100
    buy=sell=50.0; rb=[]; rs=[]
    if e50>e200: buy+=18; sell-=18; rb.append("EMA50 > EMA200")
    else: sell+=18; buy-=18; rs.append("EMA50 < EMA200")
    if rr>=55: buy+=12; rb.append(f"RSI14 {rr:.1f}")
    elif rr<=45: sell+=12; rs.append(f"RSI14 {rr:.1f}")
    if mom>0: buy+=min(10,abs(mom)*250); rb.append("positive momentum")
    elif mom<0: sell+=min(10,abs(mom)*250); rs.append("negative momentum")
    recent=c[-30:]; price=c[-1]
    if price<=min(recent)*1.003: buy+=8; rb.append("near support")
    if price>=max(recent)*.997: sell+=8; rs.append("near resistance")
    direction="BUY" if buy>=sell else "SELL"
    return {"symbol":symbol,"direction":direction,"confidence":round(max(buy,sell),1),"duration":duration,
            "price":price,"ema50":e50,"ema200":e200,"rsi14":rr,"momentum_pct":mom,
            "reason":" + ".join(rb if direction=="BUY" else rs) or "technical conditions",
            "updated_at":time.time(),"auto_trade":False}

@app.get("/")
def home(): return FileResponse("app/static/index.html")

@app.get("/health")
def health(): return {"status":"online","bot":"Crypto.com Signal Bot V1","auto_trade":False}

@app.get("/api/signal")
def signal(symbol="BTC_USDT",duration="15m"):
    if duration not in DURATIONS: duration="15m"
    key=symbol.upper()+":"+duration; now=time.time()
    if key not in cache or now-cache[key]["updated_at"]>20:
        try: cache[key]=analyze(symbol.upper(),duration)
        except Exception as e:
            if key in cache: return {"ok":True,"stale":True,**cache[key],"error":str(e)}
            return {"ok":False,"error":str(e),"symbol":symbol,"duration":duration,"auto_trade":False}
    return {"ok":True,**cache[key]}
