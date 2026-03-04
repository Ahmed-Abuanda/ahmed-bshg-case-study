import streamlit as st
import requests

API_URL = "https://hm3yewg3c8.execute-api.eu-central-1.amazonaws.com//invoke"

st.title("Chat")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if prompt := st.chat_input("Your message"):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        with st.spinner():
            r = requests.post(
                API_URL,
                json={"question": prompt},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            answer = r.json()["response"]
            if "<response>" in answer and "</response>" in answer:
                answer = answer.split("<response>")[-1].split("</response>")[0].strip()
            st.write(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})

