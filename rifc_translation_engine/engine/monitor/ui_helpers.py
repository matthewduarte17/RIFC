"""Small Streamlit helpers shared by the monitor pages."""
from __future__ import annotations

import streamlit as st

from . import store
from .profile import Ctx, context_from


def monitor_ready() -> bool:
    """Creates the monitor tables (backing the database up first) the first time a monitor page opens."""
    if st.session_state.get("_rm_ready"):
        return True
    info = store.ensure_schema(log=lambda m: None)
    if info["created"]:
        st.toast("Monitor tables added to your database. A backup was saved first: " + str(info["backup"]))
    st.session_state["_rm_ready"] = True
    return True


def get_ctx(eng, idn, disc=None) -> Ctx:
    from ..identity import cache_file
    key = ("rm_ctx", cache_file(eng), id(disc) if disc is not None else 0)
    if st.session_state.get("_rm_ctx_key") != key:
        st.session_state["_rm_ctx"] = context_from(eng, idn, disc)
        st.session_state["_rm_ctx_key"] = key
    ctx = st.session_state["_rm_ctx"]
    from .. import recruit as R, identity as I
    ctx.shortlist = R.shortlist()           # always current: you change the shortlist often
    ctx.verified = I.verified_uids()
    return ctx
