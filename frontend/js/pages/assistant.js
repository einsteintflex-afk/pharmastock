/* AI inventory assistant: questions answered from live PharmaStock data. */

import { api, escapeHtml, html, mount, pageHeader, SafeHtml } from "../core.js";

const EXAMPLES = [
    "Which medicines are at highest expiry risk?",
    "What needs reordering?",
    "Which medicines are slow-moving?",
    "What stock is currently low?",
    "Which batches of Paracetamol should be used first?",
    "What is the value of stock approaching expiry?",
];

// Conversation is kept for this browser tab only.
const history = [];

/* Minimal, safe rendering: escape first, then **bold** and line breaks / lists. */
function formatAnswer(text) {
    const lines = escapeHtml(text).split("\n");
    let out = "";
    let inList = false;
    for (const raw of lines) {
        const line = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        const item = line.match(/^\s*(?:[-•]|\d+\.)\s+(.*)$/);
        if (item) {
            if (!inList) { out += "<ul>"; inList = true; }
            out += `<li>${item[1]}</li>`;
        } else {
            if (inList) { out += "</ul>"; inList = false; }
            if (line.trim()) out += `<p>${line}</p>`;
        }
    }
    if (inList) out += "</ul>";
    return new SafeHtml(out);
}

export async function render(ctx) {
    const status = await api("/assistant/status");
    if (!ctx.isCurrent()) return;

    mount(ctx.main, html`
        ${pageHeader("AI Inventory Assistant", status.engine === "claude"
            ? `Answers from live PharmaStock data · ${status.model}`
            : "Answers from live PharmaStock data · built-in engine (set ANTHROPIC_API_KEY to enable Claude)")}
        <section class="section chat">
            <div class="chat-log" id="chat-log" aria-live="polite"></div>
            <div class="examples">${EXAMPLES.map(q => html`<button type="button" class="view-btn example" data-q="${q}">${q}</button>`)}</div>
            <form id="chat-form" class="chat-form">
                <label class="sr-only" for="chat-input">Question</label>
                <input id="chat-input" maxlength="1000" autocomplete="off" placeholder="Ask about expiry, stock, reorders, FEFO, value…">
                <button type="submit" class="refresh-btn primary">Ask</button>
            </form>
            <p class="method">Decision support only — figures come from the database; purchasing, disposal and clinical decisions remain with pharmacy staff.</p>
        </section>`);

    const log = ctx.main.querySelector("#chat-log");
    const input = ctx.main.querySelector("#chat-input");
    const form = ctx.main.querySelector("#chat-form");

    const drawLog = () => {
        mount(log, history.length ? history.map(m => html`
            <div class="bubble ${m.role}">
                ${m.role === "assistant" ? formatAnswer(m.content) : html`<p>${m.content}</p>`}
                ${m.meta ? html`<small>${m.meta}</small>` : ""}
            </div>`) : html`<div class="loading">Ask a question, or choose an example below.</div>`);
        log.scrollTop = log.scrollHeight;
    };

    const ask = async question => {
        if (!question.trim()) return;
        const prior = history.map(({ role, content }) => ({ role, content })).slice(-6);
        history.push({ role: "user", content: question });
        history.push({ role: "assistant", content: "Checking the data…" });
        drawLog();
        const button = form.querySelector("button");
        button.disabled = true;
        try {
            const result = await api("/assistant/ask", { method: "POST", body: { question, history: prior } });
            history[history.length - 1] = {
                role: "assistant", content: result.answer,
                meta: `${result.engine === "claude" ? "Claude" : "Built-in engine"} · data used: ${(result.tools_used || []).join(", ") || "none"}${result.notice ? ` · ${result.notice}` : ""}`,
            };
        } catch (error) {
            history[history.length - 1] = { role: "assistant", content: `Sorry — ${error.message}` };
        } finally {
            button.disabled = false;
            drawLog();
            input.focus();
        }
    };

    form.addEventListener("submit", event => {
        event.preventDefault();
        const question = input.value;
        input.value = "";
        ask(question);
    });
    ctx.main.querySelectorAll(".example").forEach(b => b.addEventListener("click", () => ask(b.dataset.q)));
    drawLog();
    input.focus();
}
