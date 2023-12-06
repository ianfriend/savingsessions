from datetime import datetime
from multiprocessing import Value
import numpy as np
import pendulum
import streamlit as st

from api import (
    API,
    AuthenticationError,
    ElectricityMeterPoint,
    SavingSession,
)


@st.cache_data(ttl=None)  # never expire
def get_product(code: str):
    api = API()  # unauthenticated
    return api.energy_product(code)


def weekday(day):
    """True if day is a weekday"""
    return pendulum.MONDAY <= day.day_of_week <= pendulum.FRIDAY


def phh(hh: int):
    return pendulum.duration(minutes=hh * 30)


class Readings:
    """Cached table of readings"""

    def __init__(self, meter_point: ElectricityMeterPoint):
        self.meter_point = meter_point
        self.requested = set()
        self.hh = {}

    def get_readings(self, api: API, ts: datetime, hh: int, debug):
        half_hours = list(pendulum.period(ts, ts + phh(hh - 1)).range("minutes", 30))
        if not self.requested.issuperset(half_hours):
            start_at = ts - phh(100 - hh)
            debug(f"Fetching {self.meter_point.mpan} readings from {start_at}")

            # Request readings and cache the lot
            readings = api.half_hourly_readings(
                mpan=self.meter_point.mpan,
                meter=self.meter_point.meters[0].id,
                start_at=start_at,
                first=100,
                before=None,
            )
            if readings:
                debug(
                    f"Received {len(readings)} readings from {readings[0].startAt} to {readings[-1].endAt}"
                )
                self.requested.update(
                    pendulum.period(start_at, readings[-1].startAt).range("minutes", 30)
                )
            else:
                debug("Received no readings")
                self.requested.update(
                    pendulum.period(start_at, start_at + phh(99)).range("minutes", 30)
                )

            for reading in readings:
                self.hh[reading.startAt] = reading.value

        try:
            values = [self.hh[t] for t in half_hours]
            return np.array(values)
        except KeyError:
            raise ValueError("missing readings")


class Calculation:
    def __init__(self, ss: SavingSession) -> None:
        self.ss = ss
        self.session_import = None
        self.session_export = None
        self.baseline_days = []
        self.baseline_import = None
        self.baseline_export = None
        self.baseline = None
        self.kwh = None
        self.points = None

    def calculate(
        self,
        api: API,
        sessions: list[SavingSession],
        import_readings: Readings,
        export_readings: Readings | None,
        tick,
        debug,
    ):
        # Baseline from meter readings from the same time as the Session over the past 10 weekdays (excluding any days with a Saving Session),
        # past 4 weekend days if Saving Session is on a weekend.
        days_required = 10 if weekday(self.ss.startAt) else 4
        previous_session_days = {ss.startAt.date() for ss in sessions}
        previous = pendulum.period(
            self.ss.startAt.subtract(days=1), self.ss.startAt.subtract(days=61)
        )

        try:
            self.session_import = import_readings.get_readings(
                api, self.ss.startAt, self.ss.hh, debug
            )
            debug(f"session import: {self.session_import}")
        except ValueError:
            # incomplete, but useful to still calculate baseline
            debug("session incomplete")
        next(tick)

        if export_readings:
            try:
                self.session_export = export_readings.get_readings(
                    api, self.ss.startAt, self.ss.hh, debug
                )
                debug(f"session export: {self.session_export}")
            except ValueError:
                debug("missing export readings")
        next(tick)

        days = 0
        baseline_import = []
        baseline_export = []
        for dt in previous.range("days"):
            if weekday(dt) != weekday(self.ss.startAt):
                continue
            if dt.date() in previous_session_days:
                continue

            try:
                import_values = import_readings.get_readings(api, dt, self.ss.hh, debug)
                baseline_import.append(import_values)
                debug(f"baseline day #{days}: {dt} import: {import_values}")
            except ValueError:
                debug(f"skipped day: {dt} missing readings")
                continue
            next(tick)

            if export_readings:
                try:
                    export_values = export_readings.get_readings(
                        api, dt, self.ss.hh, debug
                    )
                    baseline_export.append(export_values)
                    debug(f"baseline day #{days}: {dt} export: {export_values}")
                except ValueError:
                    debug(f"baseline day: {dt} missing export readings")
            next(tick)

            self.baseline_days.append(dt)
            days += 1
            if days == days_required:
                break

        if baseline_import:
            self.baseline_import = np.asarray(baseline_import)
            self.baseline = self.baseline_import.mean(axis=0)
            if baseline_export:
                self.baseline_export = np.asarray(baseline_export)
                self.baseline = self.baseline - self.baseline_export.mean(axis=0)

            if self.session_import is not None:
                session = self.session_import
                if self.session_export is not None:
                    session = session - self.session_export
                # saving is calculated per settlement period (half hour), and only positive savings considered
                self.kwh = (self.baseline - session).clip(min=0)
                self.points = (
                    np.round(self.kwh * self.ss.rewardPerKwhInOctoPoints / 8).astype(
                        int
                    )
                    * 8
                )

    def row(self):
        ret = {
            "session": self.ss.startAt,
        }
        if self.session_import is not None:
            ret["import"] = self.session_import.sum()
        if self.session_export is not None:
            ret["export"] = self.session_export.sum()
        if self.baseline is not None:
            ret["baseline"] = self.baseline.sum()
        if self.kwh is not None:
            ret["saved"] = self.kwh.sum()
            reward = int(self.points.sum())
            ret["reward"] = reward
            ret["earnings"] = reward / 800
        return ret


def error(msg: str):
    st.error(msg, icon="⚠️")
    st.stop()


def debug_message(msg):
    st.write(msg)


def debug_noop(msg):
    pass


def main():
    debug = (
        debug_message if "debug" in st.experimental_get_query_params() else debug_noop
    )
    st.set_page_config(page_icon="🐙", page_title="Octopus Saving Sessions calculator")
    st.header("🐙 Octopus Saving Sessions calculator")

    st.subheader("Your Octopus API Key")
    st.markdown(
        "Find this in your online dashboard: https://octopus.energy/dashboard/developer/"
    )
    if "api_key" not in st.session_state and (
        api_key := st.experimental_get_query_params().get("api_key")
    ):
        st.session_state["api_key"] = api_key[0]
    api_key = st.text_input("API key:", key="api_key", placeholder="sk_live_...")
    if not api_key:
        st.info(
            "This app never stores your API key. If you have any concerns you can check out the [source code](https://github.com/barnybug/savingsessions) for the app, and please by all means 'Regenerate' your key at the link above after using this."
        )
        st.stop()

    if st.experimental_get_query_params().get("api_key") != api_key:
        params = st.experimental_get_query_params() | {"api_key": api_key}
        st.experimental_set_query_params(**params)

    st.info("Tip: bookmark this url to return with your API key remembered.", icon="🔖")

    bar = st.progress(0, text="Authenticating...")

    api = API()
    try:
        api.authenticate(api_key)
    except AuthenticationError:
        error("Authentication error, check your API key")

    bar.progress(0.05, text="Getting account...")
    accounts = api.accounts()
    if not accounts:
        error("No accounts found")
    account = accounts[0]
    debug(account)

    bar.progress(0.07, text="Getting sessions...")
    res = api.saving_sessions(account.number)
    debug(res)
    if not res.hasJoinedCampaign:
        error("Sorry, it looks like you've not joined saving sessions.")
    if not res.signedUpMeterPoint:
        error("Sorry, it looks like you haven't a meter point for saving sessions.")
    now = pendulum.now()
    sessions = [
        session
        for session in res.sessions
        if session.id in res.joinedEvents or session.startAt > now
    ]
    if not sessions:
        error("Not joined any saving sessions yet.")
    import_mpan = res.signedUpMeterPoint or ""

    bar.progress(0.1, text="Getting meters...")
    agreements = api.agreements(account.number)
    if not agreements:
        error("No agreements on account")

    bar.progress(0.15, text="Getting tariffs...")
    export_mpan = None
    mpans: dict[str, ElectricityMeterPoint] = {}
    for agreement in agreements:
        debug(agreement)
        mpans[agreement.meterPoint.mpan] = agreement.meterPoint
        if agreement.meterPoint.mpan == import_mpan:
            continue
        # Find export meter
        product = get_product(agreement.tariff.productCode)
        if product.direction == "EXPORT":
            export_mpan = agreement.meterPoint.mpan

    import_readings = Readings(mpans[import_mpan])
    if export_mpan:
        export_readings = Readings(mpans[export_mpan])
    else:
        st.info("Import meter only", icon="ℹ️")
        export_readings = None
    debug(mpans)

    calcs = []
    rows = []
    total_ticks = 22

    def tick(message, start, end):
        for i in range(total_ticks):
            bar.progress(start + (end - start) * i / total_ticks, text=message)
            yield
        while True:
            yield

    placeholder = st.empty()

    ticks_per_session = 0.8 / len(sessions)
    for i, ss in enumerate(sessions):
        start = 0.2 + i * ticks_per_session
        ticks = tick(
            f"Getting readings for session #{i+1} ({ss.startAt:%b %d})...",
            start,
            start + ticks_per_session,
        )
        debug(f"session: {ss}")
        calc = Calculation(ss)
        calc.calculate(api, sessions, import_readings, export_readings, ticks, debug)
        calcs.append(calc)
        rows.append(calc.row())

        # Update in place
        with placeholder.container():
            st.subheader("Results")
            st.dataframe(
                rows,
                column_config={
                    "session": st.column_config.DatetimeColumn(
                        "Session", format="YYYY/MM/DD HH:mm"
                    ),
                    "import": st.column_config.NumberColumn(
                        "Imported", format="%.2f kWh"
                    ),
                    "export": st.column_config.NumberColumn(
                        "Exported", format="%.2f kWh"
                    ),
                    "baseline": st.column_config.NumberColumn(
                        "Baseline", format="%.2f kWh"
                    ),
                    "saved": st.column_config.NumberColumn("Saved", format="%.2f kWh"),
                    "reward": st.column_config.NumberColumn("Reward"),
                    "earnings": st.column_config.NumberColumn(
                        "Earnings", format="£%.2f"
                    ),
                },
            )

        # Session breakdown
        with st.expander(f"Session {ss.startAt:%b %d %Y} breakdown"):
            timestamps = [
                ts.strftime("%H:%M")
                for ts in pendulum.period(ss.startAt, ss.endAt - phh(1)).range(
                    "minutes", 30
                )
            ]
            days = [f"{day:%b %d}" for day in calc.baseline_days]

            if calc.baseline_import is not None:
                data = np.r_[
                    [timestamps],
                    calc.baseline_import,
                ].T
                st.dataframe(
                    data,
                    column_config={
                        str(i): s for i, s in enumerate(["Baseline import"] + days)
                    },
                )

            if calc.baseline_export is not None:
                data = np.r_[
                    [timestamps],
                    calc.baseline_export,
                ].T
                st.dataframe(
                    data,
                    column_config={
                        str(i): s for i, s in enumerate(["Baseline export"] + days)
                    },
                )

            data = {"Time": timestamps}
            if calc.baseline_import is not None:
                data["Baseline import"] = calc.baseline_import.mean(axis=0).round(3)
            if calc.baseline_export is not None:
                data["Baseline export"] = calc.baseline_export.mean(axis=0).round(3)
            if calc.session_import is not None:
                data["Session import"] = calc.session_import
            if calc.session_export is not None:
                data["Session export"] = calc.session_export
            if calc.kwh is not None:
                data["Net (kWh)"] = calc.kwh.round(3)
                data["Points"] = calc.points

            st.dataframe(
                np.asarray(list(data.values())).T,
                column_config={str(i): header for i, header in enumerate(data.keys())},
            )

    bar.progress(1.0, text="Done")

    for row in rows:
        if "reward" in row:
            continue
        ts = row["session"]
        st.info(f"Session on {ts:%Y/%m/%d} is awaiting readings...", icon="⌛")


if __name__ == "__main__":
    main()
