const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const babel = require("../web_ui/static/js/node_modules/@babel/core");

const source = fs.readFileSync(
  path.join(__dirname, "../web_ui/static/js/app.js"),
  "utf8",
);
const ast = babel.parseSync(source, { parserOpts: { plugins: ["jsx"] } });
const declarations = new Map();
function walk(node) {
  if (!node || typeof node !== "object") return;
  if (node.type === "VariableDeclarator" && node.id.type === "Identifier")
    declarations.set(node.id.name, node.init);
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "tokens", "comments"].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object") walk(value);
  }
}
walk(ast);

test("Prepare Images adds its flag for that execution and preserves upload arguments", () => {
  const node = declarations.get("buildExecutionArguments");
  const build = vm.runInNewContext(`(${source.slice(node.start, node.end)})`);
  const customArgs = "--screens 6 --trackers AITHER";
  assert.equal(
    build(customArgs, "--anon", true),
    `${customArgs} --anon --imagehost-upload-only`,
  );
  assert.equal(build(customArgs, "--anon", false), `${customArgs} --anon`);
  assert.equal(build("", "", true), "--imagehost-upload-only");
});

test("the argument picker exposes the standalone mode alongside skip-imagehost-upload", () => {
  const groups =
    declarations.get("ARGUMENT_GROUPS") ||
    [...declarations.values()].find(
      (node) =>
        node?.type === "ArrayExpression" &&
        source
          .slice(node.start, node.end)
          .includes('title: "Screenshots / Images"'),
    );
  assert.ok(groups);
  const text = source.slice(groups.start, groups.end);
  assert.match(text, /--skip-imagehost-upload/);
  assert.match(text, /--imagehost-upload-only/);
});

test("Prepare Images uses the same disabled state and execution controller as uploads", () => {
  const node = declarations.get("renderPrepareImagesButton");
  const text = source.slice(node.start, node.end);
  assert.match(text, /disabled=\{executeDisabled\}/);
  assert.match(text, /executeCommand\(\{ imagehostOnly: true \}\)/);
  assert.match(text, /Schedule Images/);
});

test("image-only execution does not carry into the next normal execution", async () => {
  const calls = [];
  const context = vm.createContext({
    screenshotMode: null,
    API_BASE: "/api",
    window: {},
    isExecuting: false,
    activeTabIdRef: { current: "tab-1" },
    selectedPaths: [{ path: "/media/release.mkv", args: "--screens 6" }],
    selectedPath: "",
    manualPathsText: null,
    setUploadTabs: () => {},
    setPathInputError: () => {},
    apiFetch: async () => ({
      ok: true,
      json: async () => ({ success: true, path: "/media/release.mkv" }),
    }),
    getTabRuntime: () => ({ output: { replaceChildren: () => {} } }),
    getUploadTabTitle: () => "Release",
    updateTabRun: () => {},
    reportTabPathError: (_id, message) => {
      throw new Error(message);
    },
    executeSinglePath: async (...args) => {
      calls.push(args);
    },
  });
  const node = declarations.get("executeCommand");
  const execute = vm.runInContext(
    `(${source.slice(node.start, node.end)})`,
    context,
  );
  await execute({ imagehostOnly: true });
  await execute();
  assert.deepEqual(
    calls.map((args) => args[5]),
    [true, false],
  );
  assert.ok(calls.every((args) => args[2] === "--screens 6"));
});
