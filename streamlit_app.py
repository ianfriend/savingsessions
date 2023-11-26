from typing import Any, cast
from datetime import datetime
from pathlib import Path
import pendulum
import streamlit as st

from api import API, AuthenticationError, ElectricityMeterPoint


# timestamp, session length (half hours), points awarded per kwh saved
SAVING_SESSIONS = [
    ("2023-11-16 17:30", 2, 1800),
]


def total(api: API, meter_point: ElectricityMeterPoint, ts: datetime, hh: int) -> float:
    readings = api.half_hourly_readings(
        mpan=meter_point.mpan,
        meter=meter_point.meters[0].id,
        start_at=ts,
        first=hh,
    )
    if len(readings) == 0:
        raise ValueError("missing readings")
    total = sum(reading.value for reading in readings)
    return total


def error(msg: str):
    st.error(msg, icon="⚠️")
    st.stop()


def main():
    debug = "debug" in st.experimental_get_query_params()

    st.set_page_config(page_icon="🐙", page_title="Octopus Saving Sessions calculator")
    st.header("🐙 Octopus Saving Sessions calculator")

    st.subheader("Your Octopus API Key")
    st.markdown(
        "Find this in your online dashboard: https://octopus.energy/dashboard/developer/"
    )
    api_key = Path("api_key.txt")
    value = api_key.read_text() if api_key.exists() else ""
    api_key = st.text_input("API key:", value=value, placeholder="sk_live_...")
    if not api_key:
        st.stop()

    bar = st.progress(0, text="Authenticating...")

    api = API(api_key)
    try:
        api.authenticate()
    except AuthenticationError:
        error("Authentication error, check your API key")

    bar.progress(0.2, text="Getting account...")
    accounts = api.accounts()
    if not accounts:
        error("No accounts found")
    account = accounts[0]

    if debug:
        st.write(account)
    bar.progress(0.4, text="Getting meters...")
    agreements = api.agreements(account.number)
    if debug:
        for agreement in agreements:
            st.write(agreement)
    if not agreements:
        error("No agreements on account")

    bar.progress(0.5, text="Getting tariffs...")
    mpans: dict[str, ElectricityMeterPoint] = {}
    for agreement in agreements:
        product = api.energy_product(agreement.tariff.productCode)
        if product.direction in mpans:
            st.warning(
                "Multiple %s meterpoints, using first" % product.direction, icon="⚠️"
            )
        else:
            mpans[product.direction] = agreement.meterPoint
            if len(agreement.meterPoint.meters) > 1:
                st.warning(
                    "Meterpoint %s has multiple meters, using first"
                    % agreement.meterPoint.mpan,
                    icon="⚠️",
                )
        if debug:
            st.write(product)

    if "IMPORT" not in mpans:
        error("Import meterpoint not found")

    if "EXPORT" not in mpans:
        st.info("Import meter only", icon="ℹ️")

    bar.progress(0.8, text="Getting readings...")
    rows = []
    missing = []
    for ts, hh, reward in SAVING_SESSIONS:
        ts = cast(datetime, pendulum.parser.parse(ts))
        row: dict[str, Any] = {"timestamp": ts}
        try:
            row["import"] = total(api, mpans["IMPORT"], ts, hh)
            if meter_point := mpans.get("EXPORT"):
                row["export"] = total(api, meter_point, ts, hh)
                row["saved"] = row["export"] - row["import"]
            else:
                row["saved"] = -row["import"]
            row["reward"] = max(int(row["saved"] * reward), 0)
            row["earnings"] = row["reward"] / 800
            rows.append(row)
        except ValueError:
            missing.append(ts)

    bar.progress(1.0, text="Done")
    st.subheader("Results")

    st.dataframe(
        rows,
        column_config={
            "timestamp": st.column_config.DatetimeColumn(
                "Session", format="YYYY/MM/DD HH:mm"
            ),
            "import": st.column_config.NumberColumn("Imported", format="%.2f kWh"),
            "export": st.column_config.NumberColumn("Exported", format="%.2f kWh"),
            "saved": st.column_config.NumberColumn("Saved", format="%.2f kWh"),
            "reward": st.column_config.NumberColumn("Reward"),
            "earnings": st.column_config.NumberColumn("Earnings", format="£%.2f"),
        },
    )
    for ts in missing:
        st.info(f"Session on {ts:%Y/%m/%d} is awaiting readings...", icon="⌛")


main()
