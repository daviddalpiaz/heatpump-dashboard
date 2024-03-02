# analysis imports
import pandas as pd
import plotnine as pn
from prophet import Prophet

# weather data imports
import openmeteo_requests
import requests_cache
from retry_requests import retry

# shiny imports
from shiny import App, Inputs, Outputs, Session, reactive, render, req, ui
from ipyleaflet import Map, Marker
from shinywidgets import output_widget, render_widget

# deal with future warnings from prophet
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

# read city information and make list for selecting
city_data = pd.read_csv("data/cities.csv")
city_list = city_data["city_state"].to_list()

# setup the open-meteo api client with cache and retry on error
cache_session = requests_cache.CachedSession(".cache", expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

# base url for open-meteo api
url = "https://archive-api.open-meteo.com/v1/archive"

# define sidebar to collect input and display location information
sidebar = ui.sidebar(
    ui.input_selectize(
        id="city",
        label="City",
        choices=city_list,
        selected="Urbana, Illinois",
    ),
    ui.HTML("<center>"),
    ui.output_text("coordinates"),
    ui.HTML("</center>"),
    ui.input_date_range(
        id="dates",
        label="Dates",
        start="2022-01-01",
        end="2024-01-01",
        min="2020-01-01",
        max="2024-01-01",
    ),
    ui.input_numeric(
        "forecast_years",
        "Years to Forecast",
        value=1,
        min=1,
        max=5,
    ),
    ui.input_radio_buttons(
        id="forecast_trend",
        label="Forecast Trend",
        choices={"flat": "Flat", "linear": "Linear"},
    ),
    ui.input_radio_buttons(
        id="units",
        label="Units",
        choices={"fahrenheit": "Fahrenheit", "celsius": "Celsius"},
    ),
    ui.input_slider(
        id="temp",
        label="Plot Temperature",
        value=5,
        min=-15,
        max=50,
    ),
    ui.markdown("Plot Options"),
    ui.input_checkbox(
        id="roll_week",
        label="Weekly Rolling Average",
        value=False,
    ),
    ui.input_checkbox(
        id="roll_month",
        label="Monthly Rolling Average",
        value=False,
    ),
    ui.input_slider(
        id="temp_range",
        label="Table Temperatures",
        min=-25,
        max=60,
        value=[0, 15],
    ),
    ui.hr(),
    output_widget("map"),
    width=350,
    bg="#f8f8f8",
    open="always",
)

# define historical panel for main content
historical_panel = (
    ui.output_plot("plot_weather"),
    ui.hr(),
    ui.output_data_frame("table_weather"),
)

# define forecast panel for main content
forecast_panel = (
    ui.output_plot("plot_forecast"),
    ui.hr(),
    ui.output_data_frame("table_forecast"),
)

# markdown for about page
about = """
This is some text!
"""

# define main content card
card = ui.page_navbar(
    ui.nav_panel("Historical", historical_panel),
    ui.nav_panel("Forecast", forecast_panel),
    ui.nav_panel("About", about),
    id="tab",
)

# define the overall application ui
app_ui = ui.page_sidebar(sidebar, card, title="Daily Heat Pump Efficiency Counter")


# define server functions
def server(input: Inputs, output: Outputs, session: Session):

    @reactive.calc
    def get_input_lat_lon():
        req(len(input.city()) > 0)
        city_df = city_data[city_data["city_state"] == input.city()]
        city_lat = city_df["lat"].iloc[0]
        city_lng = city_df["lng"].iloc[0]
        return city_lat, city_lng

    @reactive.calc
    def make_weather_request():
        lat, lng = get_input_lat_lon()
        dates = input.dates()
        date_start = dates[0].strftime("%Y-%m-%d")
        date_end = dates[1].strftime("%Y-%m-%d")
        params = {
            "latitude": lat,
            "longitude": lng,
            "start_date": date_start,
            "end_date": date_end,
            "daily": "temperature_2m_min",
            "timezone": "UTC",
            "temperature_unit": input.units(),
        }
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        return response

    @reactive.calc
    def get_lat_lng():
        response = make_weather_request()
        lat = response.Latitude()
        lng = response.Longitude()
        return lat, lng

    @render.text
    def coordinates():
        lat, lng = get_lat_lng()
        return f"{lat.__round__(4)}°N, {lng.__round__(4)}°E"

    @render_widget
    def map():
        lat, lng = get_lat_lng()
        m = Map(center=(lat, lng), zoom=12)
        m.add(Marker(location=(lat, lng)))
        m.layout.height = "200px"
        return m

    @reactive.calc
    def get_weather():
        response = make_weather_request()
        daily = response.Daily()
        daily_temperature_2m_min = daily.Variables(0).ValuesAsNumpy()
        daily_data = {
            "date": pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left",
            )
        }
        daily_data["temperature_2m_min"] = daily_temperature_2m_min
        df = pd.DataFrame(data=daily_data)
        df = df.rename(columns={"date": "ds", "temperature_2m_min": "y"})
        return df

    @reactive.effect
    def update_temp():
        if input.units() == "celsius":
            temp_value = -15
            temp_min = -25
            temp_max = 10
        else:
            temp_value = 5
            temp_min = -15
            temp_max = 50
        ui.update_slider("temp", value=temp_value, min=temp_min, max=temp_max)

    @reactive.effect
    def update_temp_range():
        if input.units() == "celsius":
            temp_range_value = [-20, -10]
            temp_range_min = -30
            temp_range_max = 15
        else:
            temp_range_value = [0, 15]
            temp_range_min = -25
            temp_range_max = 60
        ui.update_slider(
            "temp_range",
            value=temp_range_value,
            min=temp_range_min,
            max=temp_range_max,
        )

    @render.plot
    def plot_weather():
        req(input.temp() is not None)
        df = get_weather()
        y_min = df["y"].min() * 1.1
        y_max = df["y"].max() * 1.1
        df["cold"] = df["y"].apply(lambda x: 1 if x < input.temp() else 0)
        df["cold"] = df["cold"].astype("category")
        if input.units() == "celsius":
            unit = "C"
        else:
            unit = "F"
        p = (
            pn.ggplot(df)
            + pn.aes(x="ds", y="y", color="cold")
            + pn.geom_point(show_legend=False)
            + (
                pn.geom_smooth(
                    method="mavg",
                    method_args={"window": 7, "center": True},
                    color="darkorange",
                    se=False,
                    na_rm=True,
                )
                if input.roll_week()
                else None
            )
            + (
                pn.geom_smooth(
                    method="mavg",
                    method_args={"window": 30, "center": True},
                    color="dodgerblue",
                    se=False,
                    na_rm=True,
                )
                if input.roll_month()
                else None
            )
            + pn.geom_hline(yintercept=input.temp(), color="darkgrey")
            + pn.scale_x_datetime(date_breaks="3 months", date_labels="%Y-%m")
            + pn.scale_color_manual(["black", "lightgrey"])
            + pn.theme_bw()
            + pn.xlab("")
            + pn.ylab(f"Daily Minimum Temperature °{unit}")
            + pn.ylim(y_min, y_max)
        )
        return p

    @render.data_frame
    def table_weather():
        df = get_weather()
        min_temp = input.temp_range()[0]
        max_temp = input.temp_range()[1] + 1
        data = []
        for temp in range(min_temp, max_temp):
            days_below = df[df["y"] < temp].shape[0]
            proportion_below = days_below / df.shape[0]
            data.append([temp, days_below, proportion_below])
        new_df = pd.DataFrame(data, columns=["Temp", "Days Below", "Proportion Below"])
        new_df = new_df.sort_values(by="Temp", ascending=False)
        new_df["Proportion Below"] = new_df["Proportion Below"].round(3)
        return render.DataGrid(new_df, height=None, summary=False, width="100%", row_selection_mode="multiple")

    @reactive.calc
    def forecast():
        df = get_weather()
        df["ds"] = df["ds"].dt.tz_localize(None)
        req(df.shape[0] > 364)
        m = Prophet(interval_width=0.95, growth=input.forecast_trend())
        m.fit(df)
        future = m.make_future_dataframe(periods=input.forecast_years() * 365, include_history=False)
        df = m.predict(future)
        return m, df

    @render.plot
    def plot_forecast():
        if input.units() == "celsius":
            unit = "C"
        else:
            unit = "F"
        m, df = forecast()
        p = m.plot(df, xlabel="", ylabel=f"Daily Minimum Temperature °{unit}")
        ax = p.gca()
        ax.axhline(y=input.temp(), color="darkgrey", linestyle="-")
        return p

    @render.data_frame
    def table_forecast():
        _, df = forecast()
        min_temp = input.temp_range()[0]
        max_temp = input.temp_range()[1] + 1
        data = []
        for temp in range(min_temp, max_temp):
            days_below = df[df["yhat_lower"] < temp].shape[0]
            proportion_below = days_below / df.shape[0]
            data.append([temp, days_below, proportion_below])
        new_df = pd.DataFrame(data, columns=["Temp", "Days Below", "Proportion Below"])
        new_df = new_df.sort_values(by="Temp", ascending=False)
        new_df["Proportion Below"] = new_df["Proportion Below"].round(3)
        # return new_df
        return render.DataGrid(new_df, height=None, summary=False, width="100%", row_selection_mode="multiple")


app = App(app_ui, server)

# TODO: https://simplemaps.com/data/us-cities
# TODO: only use UTC
# TODO: .gitignore
