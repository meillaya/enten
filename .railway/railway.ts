// Railway web tier for the enten scratch deployment. Railway has no Docker
// socket, so this file does not declare the worker. Set the GitHub App values
// with `railway variable set` before applying; preserve() keeps them out of
// source.

import { defineRailway, postgres, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const db = postgres("Postgres");

  const web = service("enten-web", {
    start: "node packages/ts/app/dist/server.js",
    healthcheck: "/ready",
    healthcheckTimeout: 60,
    preDeploy: "python -m enten.migrations apply --baseline-existing",
    env: {
      DATABASE_URL: db.env.DATABASE_URL,
      GITHUB_APP_ID: preserve(),
      GITHUB_APP_SLUG: preserve(),
      GITHUB_WEBHOOK_SECRET: preserve(),
      PORT: "3000",
      METRICS_PORT: "9091",
      OTEL_ENDPOINT: "http://127.0.0.1:4317",
    },
  });

  return project("enten-scratch", {
    resources: [db, web],
  });
});
