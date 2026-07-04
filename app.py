from __future__ import annotations

import io
from typing import List

import pandas as pd
import streamlit as st

from crawler import crawl_urls_iter


st.set_page_config(page_title="Bulk URL Meta Crawler", page_icon="🔎", layout="wide")
st.title("Bulk URL Meta Crawler")
st.caption("Upload URL list (CSV/TXT/XLSX), crawl with Playwright, and extract meta title + description.")


def parse_uploaded_urls(uploaded_file) -> List[str]:
    extension = uploaded_file.name.lower().rsplit(".", 1)[-1]

    if extension == "csv":
        df = pd.read_csv(uploaded_file, header=None)
        if df.empty:
            return []
        return df.iloc[:, 0].dropna().astype(str).tolist()

    if extension == "txt":
        content = uploaded_file.getvalue().decode("utf-8", errors="ignore")
        return [line.strip() for line in content.splitlines() if line.strip()]

    if extension == "xlsx":
        df = pd.read_excel(uploaded_file, header=None)
        if df.empty:
            return []
        return df.iloc[:, 0].dropna().astype(str).tolist()

    raise ValueError("Unsupported file format. Please upload CSV, TXT, or XLSX.")


def clean_urls(urls: List[str]) -> List[str]:
    header_like = {"url", "urls", "address", "link", "website"}
    cleaned: List[str] = []
    seen = set()

    for value in urls:
        candidate = (value or "").strip()
        if not candidate:
            continue

        if candidate.lower() in header_like:
            continue

        if candidate not in seen:
            seen.add(candidate)
            cleaned.append(candidate)

    return cleaned


with st.sidebar:
    st.header("Crawler Settings")
    delay_per_domain = st.number_input(
        "Delay between requests on same domain (seconds)",
        min_value=0.0,
        max_value=30.0,
        value=2.0,
        step=0.5,
        help="Prevents hitting the same domain too quickly, reducing 429/403 risk.",
    )
    timeout_ms = st.number_input(
        "Request timeout (ms)",
        min_value=1000,
        max_value=120000,
        value=20000,
        step=1000,
    )
    user_agent = st.text_input(
        "User-Agent (optional)",
        value=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    )

uploaded_file = st.file_uploader("Upload URL file", type=["csv", "txt", "xlsx"])

if uploaded_file is not None:
    try:
        urls = clean_urls(parse_uploaded_urls(uploaded_file))
    except Exception as exc:
        st.error(f"Could not parse file: {exc}")
        st.stop()

    if not urls:
        st.warning("No URLs found in the uploaded file.")
        st.stop()

    st.info(f"Loaded {len(urls)} URLs from file.")

    if st.button("Start Crawling", type="primary"):
        progress = st.progress(0)
        status_text = st.empty()
        table_placeholder = st.empty()

        results = []
        total = len(urls)
        for i, row in enumerate(
            crawl_urls_iter(
                urls,
                delay_per_domain_seconds=delay_per_domain,
                timeout_ms=int(timeout_ms),
                user_agent=user_agent.strip() or None,
            ),
            start=1,
        ):
            results.append(row)
            status_text.write(f"Crawled {i}/{total}: {row.url}")
            progress.progress(i / total)

            table_placeholder.dataframe(
                pd.DataFrame(
                    [
                        {
                            "URL Address": r.url,
                            "Meta Title": r.meta_title,
                            "Meta Description": r.meta_description,
                            "HTTP Status": r.status,
                            "Error": r.error,
                        }
                        for r in results
                    ]
                ),
                use_container_width=True,
            )

        status_text.write("Crawling completed.")

        output_df = pd.DataFrame(
            [
                {
                    "URL Address": r.url,
                    "Meta Title": r.meta_title,
                    "Meta Description": r.meta_description,
                    "HTTP Status": r.status,
                    "Error": r.error,
                }
                for r in results
            ]
        )

        st.subheader("Results")
        st.dataframe(output_df, use_container_width=True)

        csv_buffer = io.StringIO()
        output_df.to_csv(csv_buffer, index=False)
        st.download_button(
            label="Download Results as CSV",
            data=csv_buffer.getvalue(),
            file_name="meta_results.csv",
            mime="text/csv",
        )
else:
    st.write("Upload a file to start.")
