# Final-review demo

This demo scores three recorded observations from the original frozen test split.
It runs without React, FastAPI, PostgreSQL, Locust, or the original dataset.
The interface hides the recorded outcome until it is revealed.
Case 1 is a correct LOW, Case 2 a correct HIGH, and Case 3 a false warning.

## Local backup

From this folder, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

Install before review day, then only the last command is needed with the environment active.

## One-link version

Put this folder in your GitHub repository as `streamlit_demo/` and deploy
`streamlit_demo/streamlit_app.py` on Streamlit Community Cloud.
Alternatively put the four files at the root of a separate demo repository
and use `streamlit_app.py` as the entry point. Check access from a signed-out
browser before sharing. Review the three example rows before publishing.
Do not commit the virtual environment or any credentials.

## Model and data

The JSON contains the saved StandardScaler means/scales and fitted logistic
regression weights/intercept, with the original threshold and feature order.
The app evaluates the same standardization and logistic equation live. It does
not retrain and does not load a serialized Python object at runtime.
The three examples retain only model inputs, the verified label and future-window
measurements. No experiment identifiers or timestamps are included.

Suggested narration: “These are recorded inputs from the booking experiments.
The model scores them live. After predicting, I can reveal the verified outcome.”
