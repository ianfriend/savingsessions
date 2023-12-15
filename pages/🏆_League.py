import pendulum
import streamlit as st
import db


def app():
    st.set_page_config(page_icon="🏆", page_title="Saving Sessions unofficial league")
    st.header("🏆 Saving Sessions unofficial league")
    code_to_id = {row["code"]: row["id"] for row in db.saving_sessions()}
    sessions = {row["code"]: row for row in db.saving_sessions()}

    def format_code(code):
        session = sessions[code]
        timestamp = pendulum.parse(session["timestamp"])
        points = session["points"]
        return f"{timestamp:%A %d %b %Y at %H:%M} ({points} points)"

    params = st.experimental_get_query_params()
    if "session" not in st.session_state and (param := params.get("session")):
        st.session_state["session"] = param[0]

    code = st.selectbox(
        "Select session",
        list(sessions),
        format_func=format_code,
        key="session",
    )
    if not code:
        return

    if params.get("session") != code:
        params = params | {"session": code}
        st.experimental_set_query_params(**params)

    results = db.results(code_to_id[code])
    if not results:
        st.write("No entrants yet!")
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows = [
        {"position": medals.get(pos, pos)} | result
        for pos, result in enumerate(results, 1)
    ]
    st.dataframe(
        rows,
        column_config={
            "position": "Position",
            "username": "Username",
            "baseline_import": "Baseline import",
            "baseline_export": "Baseline export",
            "session_import": "Session import",
            "session_export": "Session export",
            "points": "Points",
        },
    )


app()
