import streamlit as st

from scoring import load_demo, score

st.set_page_config(page_title="Booking lock-risk demo", layout="centered")
st.title("Booking lock-risk demo")
st.caption("Recorded PostgreSQL experiments · Live model scoring")
st.info("This demo scores a recorded snapshot. It does not book seats or create live database locks.")

data = load_demo()
model = data["model"]
cases = data["cases"]
case_name = st.selectbox("Choose a recorded scenario", list(cases))
case = cases[case_name]
x = case["features"]
st.subheader("What the system knew at the time")
a, b, c = st.columns(3)
a.metric("Waiting locks", int(x["waiting_locks"]))
b.metric("Blocked sessions", int(x["blocked_sessions"]))
c.metric("Active users", int(x["active_users"]))
with st.expander("All 18 model inputs"):
    st.table([{"Measurement": name.replace("_", " "), "Value": x[name]}
              for name in model["feature_order"]])

if st.button("Predict risk", type="primary"):
    st.session_state["scored_case"] = case_name
    st.session_state["revealed_case"] = None

if st.session_state.get("scored_case") == case_name:
    probability = score(x, model)
    high = probability >= model["threshold"]
    st.subheader("Prediction for the next 30 seconds")
    a, b, c = st.columns(3)
    a.metric("Model score", f"{probability:.2%}")
    b.metric("Warning threshold", f"{model['threshold']:.0%}")
    c.metric("Warning", "HIGH" if high else "LOW")
    st.caption("The score is a model output, not a guarantee of what will happen.")
    if st.button("Reveal what happened"):
        st.session_state["revealed_case"] = case_name
    if st.session_state.get("revealed_case") == case_name:
        actual = bool(case["actual_high"])
        st.write("Recorded outcome: **High contention**" if actual else
                 "Recorded outcome: **No high contention**")
        if actual == high:
            st.success("The prediction matched the recorded outcome.")
        elif high:
            st.warning("False warning: the model said HIGH, but the outcome was negative.")
        else:
            st.warning("Missed warning: the model said LOW, but high contention followed.")
        with st.expander("Evidence from the following 30 seconds"):
            st.table([{"Measurement": name.replace("_", " "), "Value": value}
                      for name, value in case["future_evidence"].items()])

st.caption("These selected examples explain the workflow. The full held-out test is the performance evidence.")
