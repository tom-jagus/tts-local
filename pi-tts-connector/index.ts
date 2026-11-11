import MarkdownIt from "markdown-it";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TTS_URL = "http://127.0.0.1:8765/read";
const REQUEST_TIMEOUT_MS = 3000;

/**
* Replace fenced code blocks with a short spoken label.
*
* Supports backtick and tilde fences, including an unclosed fence. This is
* intentionally a lightweight Markdown treatment, not a full parser.
*/

const markdownParser = new MarkdownIt({
  html: false,
});

/**
* Convert Markdown into text suitable for speech.
*
* The parser handles Markdown structure; this renderer chooses what should
* be spoken. Code block contents and link destinations are omitted.
*/
function prepareSpeech(markdown: string): string {
  const tokens = markdownParser.parse(markdown, {});
  const output: string[] = [];

  for (const token of tokens) {
    switch (token.type) {
      case "inline": {
        const parts: string[] = [];

        for (const child of token.children ?? []) {
          switch (child.type) {
            case "text":
            case "code_inline":
              parts.push(child.content);
              break;

            case "softbreak":
              // A wrapped line within a paragraph is still one sentence.
              parts.push(" ");
              break;

            case "hardbreak":
              parts.push("\n");
              break;

            case "image":
              // For images, speak the alt text rather than the image URL.
              if (child.content) {
                parts.push(child.content);
              }
              break;
          }
        }

        output.push(parts.join(""));
        break;
      }

      case "fence": {
        const language = token.info.trim().split(/\s+/)[0];
        output.push(
          language ? `\n${language} code block.\n` : "\nCode block.\n",
        );
        break;
      }

      case "code_block":
        // Indented Markdown code blocks do not have a language label.
        output.push("\nCode block.\n");
        break;

      case "paragraph_close":
      case "heading_close":
      case "list_item_close":
      case "blockquote_close":
      case "tr_close":
        output.push("\n\n");
        break;

      case "th_close":
      case "td_close":
        output.push(". ");
        break;

      case "hr":
        output.push("\n\n");
        break;
    }
  }

  return output
    .join("")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export default function (pi: ExtensionAPI) {
  let autoRead = true;

  // Serialize requests from this Pi session so messages are submitted in order.
  // The service still owns playback and the shared queue.
  let pending: Promise<void> = Promise.resolve();

  function updateStatus(ctx: any): void {
    if (ctx.hasUI) {
      ctx.ui.setStatus("tts", autoRead ? "TTS on" : "TTS off");
    }
  }

  function enqueue(text: string, ctx: any): void {
    const spokenText = prepareSpeech(text);

    if (!spokenText) {
      return;
    }

    pending = pending.then(async () => {
      try {
        const response = await fetch(TTS_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: spokenText }),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });

        if (!response.ok) {
          let message = `HTTP ${response.status}`;

          try {
            const body = (await response.json()) as { error?: string };
            message = body.error ?? message;
          } catch {
            // Keep the HTTP status if the response is not JSON.
          }

          throw new Error(message);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);

        if (ctx.hasUI) {
          ctx.ui.notify(`TTS: ${message}`, "warning");
        }
      }
    });
  }

  pi.on("session_start", async (_event, ctx) => {
    autoRead = true;
    updateStatus(ctx);
  });

  pi.on("message_end", async (event, ctx) => {
    const message = event.message;

    if (message.role !== "assistant") return;
    if (!autoRead) return;

    // Exclude interrupted and failed messages, but include messages that
    // complete with a tool call as well as ordinary final replies.
    if (message.stopReason === "aborted" || message.stopReason === "error") {
      return;
    }

    const text = message.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n")
      .trim();

    if (text) {
      enqueue(text, ctx);
    }
  });

  pi.registerCommand("tts", {
    description: "Read the latest reply, or turn automatic reading on/off",
    handler: async (args, ctx) => {
      const option = args.trim().toLowerCase();

      if (option === "on") {
        autoRead = true;
        updateStatus(ctx);
        ctx.ui.notify("Automatic TTS is on.", "info");
        return;
      }

      if (option === "off") {
        autoRead = false;
        updateStatus(ctx);
        ctx.ui.notify("Automatic TTS is off.", "info");
        return;
      }

      if (option !== "") {
        ctx.ui.notify("Use /tts, /tts on, or /tts off.", "warning");
        return;
      }

      const branch = ctx.sessionManager.getBranch();

      for (let i = branch.length - 1; i >= 0; i--) {
        const entry = branch[i];

        if (entry.type !== "message") continue;

        const message = entry.message;
        if (message.role !== "assistant") continue;
        if (message.stopReason === "aborted" || message.stopReason === "error") {
          continue;
        }

        const text = message.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("\n")
          .trim();

        if (text) {
          enqueue(text, ctx);
          return;
        }
      }

      ctx.ui.notify("No completed assistant text found in this session.", "info");
    },
  });
}
