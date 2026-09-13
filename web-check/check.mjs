// Browser checks for one site. Usage: node check.mjs <config.json> <results.json>
//
// The config is written by qa_checks/web_check.py; the result is a list of rows
// [name, ok|null, detail] in the same shape the Python side renders everywhere else.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";
import { LinkChecker } from "linkinator";

const [configPath, resultsPath] = process.argv.slice(2);
const config = JSON.parse(readFileSync(configPath, "utf8"));
const base = new URL(config.url);
const rows = [];
const artifacts = config.artifacts;
mkdirSync(artifacts, { recursive: true });

const browser = await launch();
try {
  const routes = await discoverRoutes(browser);
  rows.push(["routes", true, `${routes.length} route(s): ${routes.slice(0, 8).join(", ")}${routes.length > 8 ? ", …" : ""}`]);

  for (const route of routes) {
    const url = new URL(route, base).href;
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    const ignorable = (text) => /favicon\.ico/.test(text);
    page.on("console", (msg) => msg.type() === "error" && !ignorable(msg.location()?.url || msg.text()) && errors.push(`console: ${msg.text()}`));
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    page.on("requestfailed", (req) => req.failure()?.errorText !== "net::ERR_ABORTED" && errors.push(`requestfailed: ${req.url()} ${req.failure()?.errorText}`));
    page.on("response", (res) => res.status() >= 400 && !ignorable(res.url()) && errors.push(`http ${res.status()}: ${res.url()}`));
    let status = 0;
    try {
      const response = await page.goto(url, { waitUntil: "load", timeout: 30000 });
      status = response ? response.status() : 0;
      await page.waitForTimeout(500);
      await page.screenshot({ path: join(artifacts, `${slug(route)}.png`), fullPage: false });
    } catch (err) {
      errors.push(`navigation: ${err.message.split("\n")[0]}`);
    }
    if (config.consoleErrors) {
      const ok = errors.length === 0 && status < 400;
      const detail = ok ? `HTTP ${status}, no console or network errors` : `HTTP ${status}; ${errors.length} problem(s): ${errors[0]}`;
      rows.push([`route ${route}`, ok, detail]);
    }
    if (config.accessibility && status < 400) {
      try {
        const result = await new AxeBuilder({ page }).analyze();
        const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        writeFileSync(join(artifacts, `${slug(route)}-axe.json`), JSON.stringify(result.violations, null, 2));
        rows.push([
          `a11y ${route}`,
          serious.length === 0,
          serious.length === 0
            ? `${result.violations.length} minor or moderate finding(s), none serious`
            : `${serious.length} serious/critical: ${serious.map((v) => v.id).join(", ")}`,
        ]);
      } catch (err) {
        rows.push([`a11y ${route}`, false, `axe failed: ${err.message.split("\n")[0]}`]);
      }
    }
    await context.close();
  }

  if (config.links) rows.push(await checkLinks());
  if (config.lighthouse) rows.push(...(await runLighthouse(routes.slice(0, config.lighthouseRoutes))));
} finally {
  await browser.close();
}

writeFileSync(resultsPath, JSON.stringify({ rows }, null, 2));

async function launch() {
  // Prefer the runner's Google Chrome so nothing is downloaded; fall back to Playwright's Chromium.
  try {
    return await chromium.launch({ channel: process.env.WEB_CHECK_BROWSER || "chrome", headless: true });
  } catch (err) {
    console.error(`chrome channel unavailable (${err.message.split("\n")[0]}); using bundled chromium`);
    return await chromium.launch({ headless: true });
  }
}

async function discoverRoutes(browser) {
  if (config.routes.length) return dedupe(config.routes);
  const found = [base.pathname || "/"];
  const sitemap = await fetchText(new URL("/sitemap.xml", base).href);
  if (sitemap) {
    for (const match of sitemap.matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/g)) {
      const loc = new URL(match[1]);
      if (loc.origin === base.origin) found.push(loc.pathname + loc.search);
    }
  } else {
    const page = await browser.newPage();
    try {
      await page.goto(base.href, { waitUntil: "load", timeout: 30000 });
      const hrefs = await page.$$eval("a[href]", (as) => as.map((a) => a.href));
      for (const href of hrefs) {
        try {
          const link = new URL(href);
          if (link.origin === base.origin && !link.hash) found.push(link.pathname + link.search);
        } catch {}
      }
    } catch {}
    await page.close();
  }
  return dedupe(found).slice(0, config.maxRoutes);
}

async function checkLinks() {
  const checker = new LinkChecker();
  const result = await checker.check({ path: base.href, recurse: true, concurrency: 10, timeout: 10000, linksToSkip: ["^mailto:", "^tel:"] });
  const broken = result.links.filter((l) => l.state === "BROKEN");
  writeFileSync(join(artifacts, "links.json"), JSON.stringify(broken, null, 2));
  return ["links", broken.length === 0, broken.length === 0 ? `${result.links.length} link(s) checked, none broken` : `${broken.length} broken: ${broken.slice(0, 3).map((l) => `${l.url} (${l.status})`).join(", ")}`];
}

async function runLighthouse(routes) {
  const { default: lighthouse } = await import("lighthouse");
  const { launch: launchChrome } = await import("chrome-launcher");
  const out = [];
  for (const route of routes) {
    const url = new URL(route, base).href;
    let chrome;
    try {
      chrome = await launchChrome({ chromeFlags: ["--headless=new", "--no-sandbox"] });
      const result = await lighthouse(url, { port: chrome.port, output: "html", logLevel: "error" });
      writeFileSync(join(artifacts, `${slug(route)}-lighthouse.html`), result.report);
      const scores = Object.fromEntries(Object.entries(result.lhr.categories).map(([k, v]) => [k, Math.round((v.score ?? 0) * 100)]));
      const below = Object.entries(scores).filter(([, s]) => s < config.lighthouseBudget).map(([k, s]) => `${k} ${s}`);
      const summary = Object.entries(scores).map(([k, s]) => `${k} ${s}`).join(", ");
      out.push([`lighthouse ${route}`, below.length === 0, `${summary} (budget ${config.lighthouseBudget})`]);
    } catch (err) {
      out.push([`lighthouse ${route}`, false, `lighthouse failed: ${err.message.split("\n")[0]}`]);
    } finally {
      await chrome?.kill();
    }
  }
  return out;
}

async function fetchText(url) {
  try {
    const res = await fetch(url);
    return res.ok ? await res.text() : "";
  } catch {
    return "";
  }
}

function dedupe(items) {
  return [...new Set(items.map((r) => r.trim()).filter(Boolean))];
}

function slug(route) {
  return route.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "root";
}
