# --- Standard Library ---
import os, json, joblib        # For file/directory handling, file/directory handling, saving/loading trained ML models efficiently

# --- Data Handling & Math ---
import numpy as np             # Numerical computations, arrays, matrix operations
import pandas as pd            # Data loading and manipulation (CSV, DataFrame)

# --- Dashboard & Visualization ---
import streamlit as st                  # Build interactive web dashboard
import plotly.express as px             # Interactive plots (pie, scatter, etc.) 
import matplotlib.pyplot as plt         # Static plots (confusion matrix, ROC, histograms)

# --- Machine Learning (scikit-learn) ---
from sklearn.model_selection import train_test_split                   # Split dataset into train/test
from sklearn.preprocessing import StandardScaler                       # Normalize/scale numeric features
from sklearn.ensemble import IsolationForest, RandomForestClassifier   # ML models
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix, RocCurveDisplay

# ----------------------------
# Streamlit page configuration
# ----------------------------
st.set_page_config(page_title="CICIDS IDS Dashboard", layout="wide")

# --- Keywords for detecting DoS/DDoS attacks in CICIDS ---
ATTACK_KEYWORDS = ["ddos", "dos", "hulk", "goldeneye", "slowloris", "slowhttptest"]

# ----------------------------------------------------------
# Helper: Convert dataset "Label" into binary {0=Benign, 1=Attack}
# ----------------------------------------------------------
def infer_binary_label(label: str) -> int:
    s = str(label).lower()
    if s == "benign":
        return 0
    return int(any(k in s for k in ATTACK_KEYWORDS))

# ----------------------------------------------------------
# Helper: Plot Confusion Matrix (Benign vs Attack)
# ----------------------------------------------------------
def plot_confusion_matrix(y_true, y_pred):
    cm = confusion_matrix(y_true, y_pred, labels=[0,1])
    fig, ax = plt.subplots(figsize=(4,4))
    ax.imshow(cm, interpolation='nearest', cmap="Blues")
    ax.set_title('Confusion Matrix')
    ax.set_xticks([0,1]); ax.set_xticklabels(['Benign','Attack'])
    ax.set_yticks([0,1]); ax.set_yticklabels(['Benign','Attack'])
    # Annotate counts in cells
    for (i,j), z in np.ndenumerate(cm):
        ax.text(j, i, f"{z}", ha='center', va='center')
    ax.set_xlabel('Predicted'); ax.set_ylabel('True')
    fig.tight_layout()
    return fig

# ----------------------------------------------------------
# Helper: Plot ROC Curve and calculate AUC score
# ----------------------------------------------------------
def plot_roc(y_true, scores, label_name):
    try:
        auc = roc_auc_score(y_true, scores)
    except Exception:
        auc = float('nan')
    fig, ax = plt.subplots(figsize=(4,4))
    RocCurveDisplay.from_predictions(y_true, scores, ax=ax, name=f"{label_name} AUC={auc:.3f}")
    ax.set_title(f'ROC Curve — {label_name}')
    fig.tight_layout()
    return fig, auc

# ----------------------------------------------------------
# Helper: Plot histogram of anomaly scores (for IsolationForest)
# ----------------------------------------------------------
def plot_hist(scores):
    fig, ax = plt.subplots(figsize=(5,3))
    ax.hist(scores, bins=40, color="purple", alpha=0.7)
    ax.set_title('Anomaly Score Histogram')
    ax.set_xlabel('Score (higher=worse)')
    ax.set_ylabel('Count')
    fig.tight_layout()
    return fig

# ----------------------------------------------------------
# Train and evaluate both IsolationForest & RandomForest
# ----------------------------------------------------------
def train_and_evaluate(df, test_size=0.2, random_state=42):
    # Ensures dataset has labels
    if "Label" not in df.columns:
        raise ValueError("CSV must contain a 'Label' column.")
    
    # Encode labels → binary
    y = df["Label"].apply(infer_binary_label).astype(int)
    # Use only numeric features for ML models
    X = df.select_dtypes(include=[np.number]).copy()
    if X.empty:
        raise ValueError("No numeric feature columns found.")

    # Train/test split (also keep original rows for later analysis)
    X_train, X_test, y_train, y_test, raw_train, raw_test = train_test_split(
        X, y, df, test_size=test_size, random_state=random_state, stratify=y
    )
    # Standardize features (mean=0, std=1)
    scaler = StandardScaler()
    scaler.fit(X_train)
    X_train_s = scaler.transform(X_train)
    X_test_s = scaler.transform(X_test)

    results = {}

    # =======================================================
    # Model 1: IsolationForest (unsupervised anomaly detection)
    # =======================================================
    benign_mask = (y_train == 0)  # train only on benign data
    iforest = IsolationForest(
        n_estimators=300,
        max_samples='auto',
        contamination='auto',
        random_state=random_state,
        n_jobs=-1
    )
    iforest.fit(X_train_s[benign_mask])  # train only on benign samples
    decision = iforest.decision_function(X_test_s)   # anomaly scores (higher = normal)
    anomaly_score = (-decision)     # flip so higher = worse
    pred_iso = (iforest.predict(X_test_s) == -1).astype(int)  # -1 → anomaly

    # Metrics
    report_iso = classification_report(y_test, pred_iso, target_names=["Benign","Attack"], output_dict=True, zero_division=0)
    roc_auc_iso = roc_auc_score(y_test, anomaly_score)

    # Collect results in DataFrame
    iso_df = raw_test.copy()
    iso_df["y_true"] = y_test.values
    iso_df["anomaly_score"] = anomaly_score
    iso_df["y_pred"] = pred_iso

    results["iso"] = {
        "report": report_iso,
        "roc_auc": roc_auc_iso,
        "df": iso_df,
        "cm": plot_confusion_matrix(y_test, pred_iso),
        "roc": plot_roc(y_test, anomaly_score, "IsolationForest")[0],
        "hist": plot_hist(anomaly_score)
    }

    # =======================================================
    # Model 2: RandomForest (supervised classification)
    # =======================================================
    rf = RandomForestClassifier(n_estimators=300, random_state=random_state, n_jobs=-1)
    rf.fit(X_train_s, y_train)
    proba_rf = rf.predict_proba(X_test_s)[:, 1]  # probability of being attack
    pred_rf = (proba_rf >= 0.5).astype(int)      # threshold 0.5

    # Metrics
    report_rf = classification_report(y_test, pred_rf, target_names=["Benign","Attack"], output_dict=True, zero_division=0)
    roc_auc_rf = roc_auc_score(y_test, proba_rf)

    # Collect results
    rf_df = raw_test.copy()
    rf_df["y_true"] = y_test.values
    rf_df["attack_probability"] = proba_rf
    rf_df["y_pred"] = pred_rf

    results["rf"] = {
        "report": report_rf,
        "roc_auc": roc_auc_rf,
        "df": rf_df,
        "cm": plot_confusion_matrix(y_test, pred_rf),
        "roc": plot_roc(y_test, proba_rf, "RandomForest")[0],
        "hist": None
    }

    return results

# ----------------------------
# Streamlit User Interface
# ----------------------------
st.title("🔍 CICIDS2017 IDS Dashboard — DoS/DDoS Detection")
st.caption("Train & compare IsolationForest (unsupervised) vs RandomForest (supervised)")

# File upload
uploaded_file = st.file_uploader("Upload CICIDS2017 CSV file (cleaned)", type=["csv"])

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    # Train both models
    with st.spinner("Training models... this may take a while ⏳"):
        results = train_and_evaluate(df)
    # Sidebar: choose which results to view
    mode = st.sidebar.radio(
        "Select Mode",
        options=["IsolationForest", "RandomForest", "Comparison Mode"],
        index=0
    )
    if mode in ["IsolationForest", "RandomForest"]:   # Single Model Mode
        model_key = "iso" if mode == "IsolationForest" else "rf"
        res = results[model_key]

        # ==== Metrics Row ====
        roc_auc = res["roc_auc"]
        report = res["report"]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("ROC-AUC", f"{roc_auc:.3f}")
        attk_f1 = report.get("Attack", {}).get("f1-score", float("nan"))
        ben_f1 = report.get("Benign", {}).get("f1-score", float("nan"))
        col2.metric("Attack F1", f"{attk_f1:.3f}")
        col3.metric("Benign F1", f"{ben_f1:.3f}")
        col4.metric("Samples (test)", f"{len(res['df']):,}")

        st.markdown("---")

        # ==== Confusion + ROC + Histogram Row ====
        c1, c2, c3 = st.columns([1,1,1])
        with c1:
            st.subheader("Confusion Matrix")
            st.pyplot(res["cm"])
        with c2:
            st.subheader("ROC Curve")
            st.pyplot(res["roc"])
        with c3:
            if model_key == "iso" and res["hist"] is not None:
                st.subheader("Anomaly Score Histogram")
                st.pyplot(res["hist"])

        st.markdown("---")

        # ==== Predictions Table + Pie chart + Download ====
        st.subheader("Interactive Predictions Table")
        df_view = res["df"]

        # Optional filtering by original dataset label
        label_options = sorted(df_view["Label"].astype(str).unique().tolist()) if "Label" in df_view.columns else []
        label_filter = st.multiselect("Filter by original Label", label_options, default=label_options)
        if label_options:
            df_view = df_view[df_view["Label"].astype(str).isin(label_filter)] if label_filter else df_view

        c4, c5 = st.columns([2,1])
        with c4:
            st.write(f"Showing **{len(df_view):,}** rows")
            st.dataframe(df_view.head(2000), use_container_width=True, height=400)
            
            # Download filtered predictions
            csv = df_view.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Download Predictions CSV", csv, file_name=f"{mode}_predictions.csv", mime="text/csv")

        with c5:
            st.subheader("Attack vs Benign — Predicted")
            if "y_pred" in df_view.columns:
                pie = px.pie(df_view, names=df_view["y_pred"].map({0:"Benign", 1:"Attack"}), title="Predicted class breakdown")
                st.plotly_chart(pie, use_container_width=True)

    elif mode == "Comparison Mode":          # Comparison mode
        st.subheader("📊 Side-by-side Comparison")

        # Table with metrics from both models
        comp_df = pd.DataFrame({
            "Model": ["IsolationForest", "RandomForest"],
            "ROC-AUC": [results["iso"]["roc_auc"], results["rf"]["roc_auc"]],
            "Attack F1": [
                results["iso"]["report"]["Attack"]["f1-score"],
                results["rf"]["report"]["Attack"]["f1-score"]
            ],
            "Benign F1": [
                results["iso"]["report"]["Benign"]["f1-score"],
                results["rf"]["report"]["Benign"]["f1-score"]
            ]
        })
        st.table(comp_df)
        
        # Side-by-side ROC curves
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("IsolationForest ROC")
            st.pyplot(results["iso"]["roc"])
        with c2:
            st.subheader("RandomForest ROC")
            st.pyplot(results["rf"]["roc"])
        # Side-by-side confusion matrices
        c3, c4 = st.columns(2)
        with c3:
            st.subheader("IsolationForest Confusion Matrix")
            st.pyplot(results["iso"]["cm"])
        with c4:
            st.subheader("RandomForest Confusion Matrix")
            st.pyplot(results["rf"]["cm"])
else:
    st.info("👆 Please upload a CICIDS2017 CSV file to start.")
