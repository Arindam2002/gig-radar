"""Topic reader: renders one study topic with highlight + note support.

The article is rendered as HTML inside a Streamlit custom component (v2) so
the page can capture text selections. A highlight is stored as the quoted
text plus ~40 characters of context on each side (see db.study_notes); the
browser re-finds it in the article on every render, so highlights survive
the daily routine rewriting parts of the topic file.

Only the highlight/note interaction lives in JavaScript; everything else on
the topic page (back link, notes list, mark-as-studied) is native Streamlit.
"""
from __future__ import annotations

import html as html_mod
import re

import markdown
import streamlit as st
from pygments.formatters import HtmlFormatter

from jobscout import graph

_EXT_LINK = re.compile(r'<a href="(https?://[^"]+)"')


def topic_html(md: str) -> str:
    """Markdown -> HTML for the reader. Topic links are expected to be
    rewritten to dashboard links (see dashboard.linkify_topics) BEFORE this
    is called; external links open in a new tab. The YAML frontmatter block
    is metadata for the graph, not prose, so it never reaches the page."""
    _, body_md = graph.split_frontmatter(md)
    body = markdown.markdown(
        body_md,
        extensions=["extra", "sane_lists", "codehilite"],
        extension_configs={"codehilite": {"css_class": "jsr-code",
                                          "guess_lang": False}},
        output_format="html5")
    return _EXT_LINK.sub(r'<a href="\1" target="_blank" rel="noopener"', body)


_CODE_CSS = HtmlFormatter(style="native").get_style_defs(".jsr-article .jsr-code")

_HTML = """
<div class="jsr-wrap">
  <div class="jsr-article" id="jsr-art"></div>
  <div class="jsr-bar" id="jsr-bar" hidden>
    <button type="button" data-act="hl">Highlight</button>
    <button type="button" data-act="note">Highlight + note</button>
  </div>
  <div class="jsr-pop" id="jsr-pop" hidden>
    <div class="jsr-pop-quote" id="jsr-pop-quote"></div>
    <textarea id="jsr-pop-text" rows="3" placeholder="Note to self (optional)"></textarea>
    <div class="jsr-pop-row">
      <button type="button" class="jsr-primary" data-act="save">Save</button>
      <button type="button" data-act="delete" hidden>Delete highlight</button>
      <button type="button" data-act="cancel">Cancel</button>
    </div>
  </div>
</div>
"""

_CSS = """
.jsr-wrap {position: relative; color: var(--st-text-color, #d7dce5);
  font-size: 1rem; line-height: 1.6;}
.jsr-article h1 {font-size: 1.4rem; font-weight: 650; margin: .2rem 0 1rem 0;
  line-height: 1.3;}
.jsr-article h2 {font-size: 1.12rem; font-weight: 600; margin: 1.6rem 0 .5rem 0;
  padding-top: .6rem; border-top: 1px solid var(--st-border-color, rgba(148,163,184,.16));}
.jsr-article h3 {font-size: 1rem; font-weight: 600; margin: 1.1rem 0 .3rem 0;}
.jsr-article p, .jsr-article ul, .jsr-article ol {margin: 0 0 .8rem 0;}
.jsr-article li {margin: .15rem 0;}
.jsr-article a {color: var(--st-link-color, #aab8f5);}
.jsr-article blockquote {margin: .6rem 0; padding: .1rem 1rem;
  border-left: 3px solid var(--st-border-color, rgba(148,163,184,.3));
  opacity: .85;}
.jsr-article code {font-family: var(--st-code-font, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: .85em; background: var(--st-code-background-color, rgba(148,163,184,.12));
  padding: .1em .3em; border-radius: 4px;}
.jsr-article pre {padding: .7rem .9rem; border-radius: 10px; overflow-x: auto;
  background: var(--st-code-background-color, #1a1e27); margin: 0 0 .9rem 0;}
.jsr-article pre code {background: none; padding: 0; font-size: .82rem;
  line-height: 1.5;}
.jsr-article table {border-collapse: collapse; margin: .4rem 0 1rem 0;
  font-size: .9rem; display: block; overflow-x: auto; max-width: 100%;}
.jsr-article th, .jsr-article td {padding: .35rem .7rem; text-align: left;
  border: 1px solid var(--st-border-color, rgba(148,163,184,.18));
  vertical-align: top;}
.jsr-article th {background: rgba(148,163,184,.08); font-weight: 600;}
.jsr-article hr {border: none; border-top: 1px solid rgba(148,163,184,.18);
  margin: 1.2rem 0;}
.jsr-article img {max-width: 100%;}
.jsr-article ::selection {background: rgba(139,156,247,.35);}
mark.jsr-hl {background: rgba(250, 204, 21, .26); color: inherit;
  border-radius: 3px; padding: 0 1px; cursor: pointer;
  transition: background .15s;}
mark.jsr-hl:hover {background: rgba(250, 204, 21, .42);}
mark.jsr-hl.jsr-noted {border-bottom: 2px solid rgba(250, 204, 21, .9);}
mark.jsr-hl.jsr-flash {background: rgba(139,156,247,.45);}
.jsr-bar, .jsr-pop {position: absolute; z-index: 50;
  background: var(--st-secondary-background-color, #1a1e27);
  border: 1px solid var(--st-border-color, rgba(148,163,184,.25));
  border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.45);}
.jsr-bar {display: flex; gap: 4px; padding: 4px;}
.jsr-bar[hidden], .jsr-pop[hidden] {display: none;}
.jsr-pop {width: min(360px, 90vw); padding: 10px;}
.jsr-pop-quote {font-size: .8rem; opacity: .7; font-style: italic;
  margin-bottom: 6px; max-height: 4.2em; overflow: hidden;}
.jsr-pop textarea {width: 100%; box-sizing: border-box; resize: vertical;
  font: inherit; font-size: .9rem; color: inherit; padding: 6px 8px;
  border-radius: 8px; border: 1px solid rgba(148,163,184,.3);
  background: var(--st-background-color, #12151c); margin-bottom: 8px;}
.jsr-pop-row {display: flex; gap: 6px; justify-content: flex-end;}
.jsr-wrap button {font: inherit; font-size: .8rem; color: inherit;
  background: rgba(148,163,184,.10); border: 1px solid rgba(148,163,184,.25);
  border-radius: 8px; padding: 4px 10px; cursor: pointer; white-space: nowrap;}
.jsr-wrap button:hover {background: rgba(148,163,184,.18);}
.jsr-wrap button.jsr-primary {background: var(--st-primary-color, #8b9cf7);
  color: #12151c; border-color: transparent; font-weight: 600;}
.jsr-wrap button[hidden] {display: none;}
"""

_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const wrap = parentElement.querySelector(".jsr-wrap")
  const art = parentElement.querySelector("#jsr-art")
  const bar = parentElement.querySelector("#jsr-bar")
  const pop = parentElement.querySelector("#jsr-pop")
  const popQuote = parentElement.querySelector("#jsr-pop-quote")
  const popText = parentElement.querySelector("#jsr-pop-text")
  const btnDelete = pop.querySelector("[data-act=delete]")
  if (!wrap || !art) return

  const CTX = 40
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim()

  // ── article text as one string, with a map back to text nodes ──
  function flatten(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    const nodes = []
    let raw = ""
    let n
    while ((n = walker.nextNode())) {
      nodes.push({ node: n, start: raw.length })
      raw += n.nodeValue
    }
    // whitespace-collapsed copy + map: normalized index -> raw index
    let text = ""
    const map = []
    let prevSpace = true
    for (let i = 0; i < raw.length; i++) {
      const ch = raw[i]
      if (/\\s/.test(ch)) {
        if (!prevSpace) { text += " "; map.push(i); prevSpace = true }
      } else { text += ch; map.push(i); prevSpace = false }
    }
    return { raw, nodes, text, map }
  }

  function rawOffset(flat, container, offset) {
    const r = document.createRange()
    r.setStart(art, 0)
    r.setEnd(container, offset)
    return r.toString().length
  }

  // wrap raw range [s, e) in <mark> elements, one per text node touched
  function wrapRange(flat, s, e, h) {
    const jobs = []
    for (const { node, start } of flat.nodes) {
      const end = start + node.nodeValue.length
      const ls = Math.max(s, start) - start
      const le = Math.min(e, end) - start
      if (le > ls) jobs.push({ node, ls, le })
    }
    for (const { node, ls, le } of jobs) {
      let target = node
      if (le < node.nodeValue.length) node.splitText(le)
      if (ls > 0) target = node.splitText(ls)
      const m = document.createElement("mark")
      m.className = "jsr-hl" + (h.note ? " jsr-noted" : "")
      m.dataset.id = String(h.id)
      m.title = h.note ? h.note : "Highlight (click to add a note)"
      target.parentNode.insertBefore(m, target)
      m.appendChild(target)
    }
  }

  function findQuote(flat, h) {
    const q = norm(h.quote)
    if (!q) return null
    const hits = []
    let from = 0
    while (true) {
      const i = flat.text.indexOf(q, from)
      if (i < 0) break
      hits.push(i)
      from = i + 1
    }
    if (!hits.length) return null
    let best = hits[0], bestScore = -1
    const pre = norm(h.prefix), suf = norm(h.suffix)
    for (const i of hits) {
      const before = flat.text.slice(Math.max(0, i - CTX), i)
      const after = flat.text.slice(i + q.length, i + q.length + CTX)
      let score = 0
      for (let k = 1; k <= Math.min(pre.length, before.length); k++) {
        if (pre[pre.length - k] === before[before.length - k]) score++; else break
      }
      for (let k = 0; k < Math.min(suf.length, after.length); k++) {
        if (suf[k] === after[k]) score++; else break
      }
      if (score > bestScore) { bestScore = score; best = i }
    }
    const s = flat.map[best]
    const e = flat.map[best + q.length - 1] + 1
    return [s, e]
  }

  // ── render ──
  art.innerHTML = data.html || ""
  const highlights = Array.isArray(data.highlights) ? data.highlights : []
  for (const h of highlights) {
    const flat = flatten(art)          // re-flatten: marks split text nodes
    const span = findQuote(flat, h)
    if (span) wrapRange(flat, span[0], span[1], h)
  }
  const byId = Object.fromEntries(highlights.map(h => [String(h.id), h]))

  // ── popover state ──
  let pending = null   // {quote, prefix, suffix} for a new highlight
  let editing = null   // id of an existing highlight

  function hideAll() { bar.hidden = true; pop.hidden = true; pending = null; editing = null }

  function place(el, rect) {
    const w = wrap.getBoundingClientRect()
    el.hidden = false
    const left = Math.max(0, Math.min(rect.left - w.left, w.width - el.offsetWidth - 4))
    el.style.left = left + "px"
    el.style.top = (rect.bottom - w.top + 8) + "px"
  }

  function openPop(rect, quote, note, isEdit) {
    bar.hidden = true
    popQuote.textContent = quote.length > 220 ? quote.slice(0, 220) + "…" : quote
    popText.value = note || ""
    btnDelete.hidden = !isEdit
    place(pop, rect)
    popText.focus()
  }

  function currentSelection() {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
    const r = sel.getRangeAt(0)
    if (!art.contains(r.startContainer) || !art.contains(r.endContainer)) return null
    const flat = flatten(art)
    const s = rawOffset(flat, r.startContainer, r.startOffset)
    const e = rawOffset(flat, r.endContainer, r.endOffset)
    const quote = norm(flat.raw.slice(s, e))
    if (!quote) return null
    return {
      quote,
      prefix: norm(flat.raw.slice(Math.max(0, s - CTX), s)),
      suffix: norm(flat.raw.slice(e, e + CTX)),
      rect: r.getBoundingClientRect(),
    }
  }

  art.onmouseup = (ev) => {
    if (ev.target.closest && ev.target.closest("mark.jsr-hl")) return
    setTimeout(() => {
      const sel = currentSelection()
      if (!sel) { bar.hidden = true; return }
      pending = sel
      pop.hidden = true
      editing = null
      place(bar, sel.rect)
    }, 10)
  }

  art.onclick = (ev) => {
    const m = ev.target.closest && ev.target.closest("mark.jsr-hl")
    if (!m) return
    ev.preventDefault()
    const h = byId[m.dataset.id]
    if (!h) return
    pending = null
    editing = h.id
    openPop(m.getBoundingClientRect(), h.quote, h.note, true)
  }

  bar.onmousedown = (ev) => ev.preventDefault()   // keep the selection alive
  bar.onclick = (ev) => {
    const act = ev.target.dataset && ev.target.dataset.act
    if (!act || !pending) return
    if (act === "hl") {
      setTriggerValue("action", { type: "add", ...pending, note: "" })
      hideAll()
      window.getSelection && window.getSelection().removeAllRanges()
    } else if (act === "note") {
      const p = pending
      openPop(p.rect, p.quote, "", false)
      pending = p
    }
  }

  pop.onclick = (ev) => {
    const act = ev.target.dataset && ev.target.dataset.act
    if (!act) return
    if (act === "cancel") { hideAll(); return }
    if (act === "save") {
      if (pending) {
        setTriggerValue("action", { type: "add", quote: pending.quote,
          prefix: pending.prefix, suffix: pending.suffix, note: popText.value.trim() })
      } else if (editing != null) {
        setTriggerValue("action", { type: "update", id: editing, note: popText.value.trim() })
      }
      hideAll()
      window.getSelection && window.getSelection().removeAllRanges()
    }
    if (act === "delete" && editing != null) {
      setTriggerValue("action", { type: "delete", id: editing })
      hideAll()
    }
  }
  popText.onkeydown = (ev) => {
    if (ev.key === "Escape") hideAll()
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) pop.querySelector("[data-act=save]").click()
  }

  const onDocDown = (ev) => {
    if (bar.contains(ev.target) || pop.contains(ev.target)) return
    if (!pop.hidden) hideAll()
  }
  document.addEventListener("mousedown", onDocDown)

  // scroll to a highlight the notes list asked for
  if (data.focus_id != null) {
    const m = art.querySelector(`mark.jsr-hl[data-id="${data.focus_id}"]`)
    if (m) {
      m.scrollIntoView({ block: "center", behavior: "smooth" })
      m.classList.add("jsr-flash")
      setTimeout(() => m.classList.remove("jsr-flash"), 1800)
    }
  }

  return () => document.removeEventListener("mousedown", onDocDown)
}
"""

_READER = st.components.v2.component(
    "jobscout_topic_reader",
    html=_HTML,
    css=_CSS + _CODE_CSS,
    js=_JS,
    # not isolated: text selection inside a shadow root is unreliable in
    # Safari, and the article should inherit the app's fonts anyway
    isolate_styles=False,
)


def topic_reader(article_html: str, highlights: list[dict], *, key: str,
                 on_action, focus_id: int | None = None):
    """Mount the reader. `on_action` runs (before the script body) whenever
    the page emits a highlight action; read it from
    st.session_state[key].action -> {"type": "add"|"update"|"delete", ...}."""
    return _READER(
        key=key,
        data={"html": article_html,
              "highlights": [{"id": h["id"], "quote": h["quote"],
                              "prefix": h.get("prefix", ""),
                              "suffix": h.get("suffix", ""),
                              "note": h.get("note", "")} for h in highlights],
              "focus_id": focus_id},
        on_action_change=on_action,
    )


def quote_preview(text: str, n: int = 140) -> str:
    text = " ".join((text or "").split())
    return html_mod.escape(text if len(text) <= n else text[:n].rstrip() + "…")
