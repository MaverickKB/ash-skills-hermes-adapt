# Compare two Node.js frameworks before building a JSON API

The API must echo a string and return HTTP 400 for invalid input, including a number in the string field. Run both candidates on Node.js 22 with the same request bodies and expected responses.

## Check sources before installing

Checked on 2026-11-23.

- Check the runtime line and record the specific version used for the comparison. [Node.js release status](https://nodejs.org/en/about/previous-releases) · [Node.js 22.23.3 release](https://github.com/nodejs/node/releases/tag/v22.23.3)
- Check Express 5.3.0 support and trace its JSON middleware to the parser. Its tests cover malformed JSON. [Express 5 API](https://expressjs.com/en/5x/api/) · [Tagged source](https://github.com/expressjs/express/blob/v5.3.0/lib/express.js) · [Tagged JSON tests](https://github.com/expressjs/express/blob/v5.3.0/test/express.json.js)
- Check Fastify 5.12.8 support and trace how it validates request bodies. Its Ajv setup can coerce input types. [Fastify support](https://fastify.dev/docs/latest/Reference/LTS/) · [Validation docs](https://fastify.dev/docs/latest/Reference/Validation-and-Serialization/) · [Tagged source](https://github.com/fastify/fastify/blob/v5.12.8/lib/validation.js) · [Tagged schema tests](https://github.com/fastify/fastify/blob/v5.12.8/test/schema-validation.test.js)

Use the registry to confirm the exact package metadata. A missing `engines` field leaves runtime support for the project's docs to establish.

```bash
npm view express@5.3.0 version engines peerDependencies license deprecated repository.url --json
npm view fastify@5.12.8 version engines peerDependencies license deprecated repository.url --json
```

## Run the same contract against both candidates

In scratch, install the exact versions and keep the lockfile for reruns.

```bash
npm install --ignore-scripts --save-exact express@5.3.0 fastify@5.12.8
npm ci --ignore-scripts
```

Create a probe that serves both implementations on loopback and runs the same five assertions with `node --test`. Use `express.json()` and a `typeof` check in the Express handler. Use a required string property in the Fastify body schema. Give both implementations the same error response, `{"error":"invalid payload"}`. Check the status and the parsed JSON for each response.

| Request | Expected | Express | Fastify default | Fastify with coercion disabled |
| --- | --- | --- | --- | --- |
| `GET /health` | `200 {"ok":true}` | Pass | Pass | Pass |
| `POST /echo` with `{"message":"hello"}` | `200 {"message":"hello"}` | Pass | Pass | Pass |
| `POST /echo` with malformed JSON | `400` and error body | Pass | Pass | Pass |
| `POST /echo` with `{}` | `400` and error body | Pass | Pass | Pass |
| `POST /echo` with `{"message":17}` | `400` and error body | Pass | Returned `200 {"message":"17"}` | Pass |

These are observed results on Node.js 22.23.3 and npm 10.9.9. The last column came from rerunning the same tests with `ajv.customOptions.coerceTypes` set to `false` in the Fastify probe.

Use Express with the explicit string check for this small API. If you already use Fastify, the tested configuration with coercion disabled also meets this contract. Keep the numeric-input assertion when you implement the endpoint.
