import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TTS_URL = "http://127.0.0.1:8765/read";
const REQUEST_TIMEOUT_MS = 3000;

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
    const spokenText = text.trim();

    if (!spokenText) {
      return;
    }

    pending = pending.then(async () => {
      try {
        const response = await fetch(TTS_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: spokenText,
            format: "markdown",
            split_headings: false,
          }),
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
