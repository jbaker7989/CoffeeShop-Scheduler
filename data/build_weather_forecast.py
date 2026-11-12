"""Build weather_forecast.xlsx: Open-Meteo historical weather joined to coffee_shop_sales.xlsx.
Run on your Mac:  python3 build_weather_forecast.py   (needs: pip install pandas openpyxl requests)
"""
import pandas as pd, requests, os
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
HERE=os.path.dirname(os.path.abspath(__file__))
SALES=os.path.join(HERE,"coffee_shop_sales.xlsx"); OUT=os.path.join(HERE,"weather_forecast.xlsx")
STORES={5:("Lower Manhattan",40.7075,-74.0113),8:("Hell's Kitchen",40.7638,-73.9918),3:("Astoria",40.7644,-73.9235)}
HOURLY="temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,rain,snowfall,snow_depth,cloud_cover,wind_speed_10m,wind_gusts_10m,weather_code"
WMO={0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",45:"Fog",48:"Depositing rime fog",51:"Light drizzle",53:"Moderate drizzle",55:"Dense drizzle",56:"Light freezing drizzle",57:"Dense freezing drizzle",61:"Slight rain",63:"Moderate rain",65:"Heavy rain",66:"Light freezing rain",67:"Heavy freezing rain",71:"Slight snow",73:"Moderate snow",75:"Heavy snow",77:"Snow grains",80:"Slight rain showers",81:"Moderate rain showers",82:"Violent rain showers",85:"Slight snow showers",86:"Heavy snow showers",95:"Thunderstorm",96:"Thunderstorm, slight hail",99:"Thunderstorm, heavy hail"}
def bucket(code):
    if code>=95: return "Storm"
    if code in (71,73,75,77,85,86): return "Snow"
    if code in (56,57,66,67): return "Freezing"
    if code>=51: return "Rain"
    if code in (45,48): return "Fog"
    return "Dry"
s=pd.read_excel(SALES)
s["hour"]=s.transaction_time.apply(lambda t:t.hour)
start,end=s.transaction_date.min().date(),s.transaction_date.max().date()
frames=[]
for sid,(nm,lat,lon) in STORES.items():
    r=requests.get("https://archive-api.open-meteo.com/v1/archive",params=dict(latitude=lat,longitude=lon,start_date=str(start),end_date=str(end),hourly=HOURLY,timezone="America/New_York",temperature_unit="fahrenheit",wind_speed_unit="mph",precipitation_unit="inch"),timeout=60)
    r.raise_for_status(); h=pd.DataFrame(r.json()["hourly"])
    h["time"]=pd.to_datetime(h.time); h.insert(0,"store_location",nm); h.insert(0,"location_id",sid)
    frames.append(h)
w=pd.concat(frames)
w["date"]=w.time.dt.normalize(); w["hour"]=w.time.dt.hour; w["day_of_week"]=w.time.dt.day_name()
w["weather_desc"]=w.weather_code.map(WMO); w["weather_bucket"]=w.weather_code.apply(bucket)
w=w[(w.hour>=6)&(w.hour<=22)]
g=s.groupby(["store_id","transaction_date","hour"]).agg(line_items=("transaction_id","count"),items=("transaction_qty","sum"),revenue=("unit_price",lambda x:(x*s.loc[x.index,"transaction_qty"]).sum())).reset_index()
j=w.merge(g,left_on=["location_id","date","hour"],right_on=["store_id","transaction_date","hour"],how="left").drop(columns=["store_id","transaction_date"])
for c in ["line_items","items","revenue"]: j[c]=j[c].fillna(0)
j["in_trading_hours"]=j.line_items>0
cols=["location_id","store_location","date","day_of_week","hour","line_items","items","revenue","temperature_2m","apparent_temperature","relative_humidity_2m","precipitation","rain","snowfall","snow_depth","cloud_cover","wind_speed_10m","wind_gusts_10m","weather_code","weather_desc","weather_bucket","in_trading_hours"]
j=j[cols].sort_values(["location_id","date","hour"])
d=j.groupby(["location_id","store_location","date","day_of_week"]).agg(line_items=("line_items","sum"),items=("items","sum"),revenue=("revenue","sum"),temp_max_f=("temperature_2m","max"),temp_min_f=("temperature_2m","min"),temp_mean_f=("temperature_2m","mean"),precip_in=("precipitation","sum"),snow_in=("snowfall","sum"),max_wind_mph=("wind_speed_10m","max"),rain_hours=("weather_bucket",lambda x:(x=="Rain").sum()),dominant_weather=("weather_bucket",lambda x:x.mode()[0])).reset_index()
readme=pd.DataFrame({"field":["Source","Units","Hours","Join","weather_bucket","Purpose","License"],
"value":["Open-Meteo Historical Weather API (ERA5 reanalysis), pulled per store coordinate","°F, inches, mph, % (local time America/New_York)","06:00–22:59 local (store operating window incl. 22:30 close)","location_id = store_id; date + hour = transaction_date + hour(transaction_time)","Dry / Fog / Rain / Freezing / Snow / Storm from WMO weather_code","Training data for the Demand Forecast agent and calibration of Demand_Adjustments multipliers","Open-Meteo data CC BY 4.0 — attribution required; free API is non-commercial"]})
wmo=pd.DataFrame([(k,v,bucket(k)) for k,v in WMO.items()],columns=["weather_code","description","bucket"])
with pd.ExcelWriter(OUT,engine="openpyxl") as x:
    readme.to_excel(x,sheet_name="README",index=False); j.to_excel(x,sheet_name="Hourly_Sales_Weather",index=False)
    d.to_excel(x,sheet_name="Daily_Sales_Weather",index=False); wmo.to_excel(x,sheet_name="Weather_Codes",index=False)
wb=load_workbook(OUT)
for ws in wb:
    for row in ws.iter_rows():
        for c in row: c.font=Font(name="Arial",size=10)
    for c in ws[1]: c.font=Font(name="Arial",size=10,bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="5B3A29")
    ws.freeze_panes="A2"
    for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=max(10,min(60,len(str(col[0].value))+2))
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if hasattr(c.value,"year"): c.number_format="yyyy-mm-dd"
            if ws[f"{c.column_letter}1"].value=="revenue": c.number_format="$#,##0.00"
wb["README"].column_dimensions["B"].width=95
wb.save(OUT); print("Wrote",OUT,len(j),"hourly rows",len(d),"daily rows")
