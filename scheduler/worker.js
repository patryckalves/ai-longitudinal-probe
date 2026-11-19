import { WorkflowEntrypoint } from "cloudflare:workers";

function brtDay(date) {
  const brt = new Date(date.getTime() - 3 * 60 * 60 * 1000);
  return brt.toISOString().slice(0, 10);
}

function randomDelaySeconds(maxSeconds) {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return Math.floor((buf[0] / 0x100000000) * maxSeconds);
}

export class CollectWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const delaySeconds = Number(event.payload?.delaySeconds || 0);
    if (delaySeconds > 0) {
      await step.sleep("wait jitter", `${delaySeconds} seconds`);
    }

    await step.do("dispatch workflow", async () => {
      if (!this.env.GITHUB_TOKEN) {
        throw new Error("Missing required secret: GITHUB_TOKEN");
      }

      const url =
        `https://api.github.com/repos/${this.env.GITHUB_REPO}` +
        `/actions/workflows/${this.env.WORKFLOW_FILE}/dispatches`;

      const resp = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "ai-longitudinal-scheduler",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: this.env.WORKFLOW_REF }),
      });

      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`GitHub dispatch failed (${resp.status}): ${detail}`);
      }
    });
  }
}

export default {
  async fetch() {
    return new Response("Not Found", { status: 404 });
  },

  async scheduled(event, env) {
    if (!env.GITHUB_TOKEN) {
      throw new Error("Missing required secret: GITHUB_TOKEN");
    }

    const now = new Date(event.scheduledTime);
    const day = brtDay(now);
    const maxSeconds = Number(env.JITTER_MAX_SECONDS || 14400);
    const delaySeconds = randomDelaySeconds(maxSeconds);

    await env.COLLECT_WORKFLOW.create({
      id: `${day}-${event.scheduledTime}`,
      params: { day, delaySeconds },
    });
  },
};
