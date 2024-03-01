import pandas as pd

df = pd.read_csv("data-raw/uscities.csv")
df = df[["city", "county_name", "state_name", "population", "lat", "lng"]]
df = df[df["population"] > 9999]
df["city_state"] = df["city"] + ", " + df["state_name"]
df = df[["city_state", "lat", "lng"]]
df.to_csv("data/cities.csv", index=False)
