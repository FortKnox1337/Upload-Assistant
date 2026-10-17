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

test("execution forwards the selected mode and per-item arguments unchanged", () => {
  const node = declarations.get("buildExecutionArguments");
  const build = vm.runInNewContext(`(${source.slice(node.start, node.end)})`);
  const customArgs = "--screens 6 --trackers AITHER";
  assert.equal(
    build(`${customArgs} --imagehost-upload-only`, "--anon"),
    `${customArgs} --imagehost-upload-only --anon`,
  );
  assert.equal(build(customArgs, "--anon"), `${customArgs} --anon`);
  assert.equal(build("", ""), "");
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

const load = (context, name) => {
  const node = declarations.get(name);
  vm.runInContext(
    `var ${name} = ${source.slice(node.start, node.end)};`,
    context,
  );
};

function setupModes() {
  const context = vm.createContext({ Set });
  for (const name of [
    "EXECUTION_MODES",
    "EXECUTION_MODE_FLAGS",
    "executionArgumentTokens",
    "getExecutionMode",
    "setExecutionModeArguments",
    "executionMenuPosition",
    "buildExecutionArguments",
  ])
    load(context, name);
  return context;
}

test("mode detection accepts CLI aliases and ignores flags inside quoted text", () => {
  const context = setupModes();
  for (const [args, expected] of [
    ["--screens 6", "normal"],
    ["--imagehost-upload-only --screens 6", "images"],
    ["--debug", "debug"],
    ["-debug -ns -siu", "debug-local"],
    ["--debug --no-seed --skip-imagehost-upload", "debug-local"],
    [
      '--description "keep --debug and --imagehost-upload-only in this text"',
      "normal",
    ],
    ['--description="keep -debug -ns -siu in this text"', "normal"],
  ])
    assert.equal(context.getExecutionMode(args).id, expected);
});

test("choosing a mode replaces its flags while preserving quoted arguments and paths", () => {
  const context = setupModes();
  const otherArgs = String.raw`--description="keep --debug here" --descfile "G:\My Files\notes.txt" --screens 6`;
  const original = `${otherArgs} --debug -debug --no-seed -siu --imagehost-upload-only`;
  for (const mode of context.EXECUTION_MODES) {
    const updated = context.setExecutionModeArguments(original, mode.id);
    assert.equal(updated, [...mode.flags, otherArgs].filter(Boolean).join(" "));
    assert.equal(context.getExecutionMode(updated).id, mode.id);
  }
  assert.equal(
    context.setExecutionModeArguments(original, "unknown"),
    original,
  );
  const unfinished = '--description "unfinished text';
  const updated = context.setExecutionModeArguments(unfinished, "debug");
  assert.equal(updated, `-debug ${unfinished}`);
  assert.equal(context.getExecutionMode(updated).id, "debug");
  assert.equal(
    context.setExecutionModeArguments(updated, "normal"),
    unfinished,
  );
});

test("selecting an execution mode only changes arguments and focuses the main button", () => {
  const context = setupModes();
  let focused = 0;
  Object.assign(context, {
    customArgs: "--screens 6",
    isExecuting: false,
    screenshotMode: null,
    isExecutionMenuOpen: true,
    setCustomArgs: (update) => {
      context.customArgs = update(context.customArgs);
    },
    setIsExecutionMenuOpen: (value) => {
      context.isExecutionMenuOpen = value;
    },
    executionButtonRef: {
      current: {
        focus: () => {
          focused++;
        },
      },
    },
    apiFetch: () => assert.fail("Selecting a mode must not start a request"),
    executeCommand: () => assert.fail("Selecting a mode must not execute"),
  });
  load(context, "selectExecutionMode");
  context.selectExecutionMode("images");
  assert.equal(context.customArgs, "--imagehost-upload-only --screens 6");
  assert.equal(context.isExecutionMenuOpen, false);
  assert.equal(focused, 1);
  context.isExecuting = true;
  context.selectExecutionMode("normal");
  assert.equal(context.getExecutionMode(context.customArgs).id, "images");
});

test("an explicit Execute action runs the selected mode; Normal Upload clears it", async () => {
  const calls = [];
  const requests = [];
  const context = setupModes();
  Object.assign(context, {
    screenshotMode: null,
    API_BASE: "/api",
    window: {},
    isExecuting: false,
    activeTabIdRef: { current: "tab-1" },
    selectedPaths: [{ path: "/media/release.mkv", args: "--anon" }],
    customArgs: "--screens 6",
    selectedPath: "",
    manualPathsText: null,
    setUploadTabs: () => {},
    setPathInputError: () => {},
    apiFetch: async (url) => {
      requests.push(url);
      return {
        ok: true,
        json: async () => ({ success: true, path: "/media/release.mkv" }),
      };
    },
    getTabRuntime: () => ({ output: { replaceChildren: () => {} } }),
    getUploadTabTitle: () => "Release",
    updateTabRun: () => {},
    reportTabPathError: (_id, message) => {
      throw new Error(message);
    },
    executeSinglePath: async (...args) => {
      calls.push(context.buildExecutionArguments(context.customArgs, args[2]));
    },
  });
  load(context, "executeCommand");
  context.customArgs = context.setExecutionModeArguments(
    context.customArgs,
    "images",
  );
  assert.equal(requests.length, 0);
  await context.executeCommand();
  assert.equal(calls[0], "--imagehost-upload-only --screens 6 --anon");
  context.customArgs = context.setExecutionModeArguments(
    context.customArgs,
    "normal",
  );
  assert.equal(requests.length, 1);
  await context.executeCommand();
  assert.equal(calls[1], "--screens 6 --anon");
});

test("main-button labels reflect modes, queues and scheduling", () => {
  const context = setupModes();
  Object.assign(context, {
    isExecuting: false,
    activeTabScheduled: false,
    uploadSlotsFull: false,
    schedulingEnabled: true,
    selectedPaths: [],
  });
  load(context, "getExecuteButtonLabel");
  for (const mode of context.EXECUTION_MODES) {
    context.selectedExecutionMode = mode;
    assert.equal(context.getExecuteButtonLabel(), mode.button);
    context.uploadSlotsFull = true;
    assert.equal(context.getExecuteButtonLabel(), mode.schedule);
    context.schedulingEnabled = false;
    assert.equal(context.getExecuteButtonLabel(), "All upload slots busy");
    context.schedulingEnabled = true;
    context.uploadSlotsFull = false;
  }
  context.selectedExecutionMode = context.EXECUTION_MODES[0];
  context.selectedPaths = [{}, {}];
  assert.equal(context.getExecuteButtonLabel(), "Execute Queue");
});

test("the menu stays in the viewport and opens above crowded controls", () => {
  const context = setupModes();
  const mobile = context.executionMenuPosition(
    { right: 350, top: 530, bottom: 578 },
    390,
    844,
  );
  assert.equal(mobile.width, 366);
  assert.equal(mobile.left, 12);
  assert.ok(mobile.bottom > 0);
  assert.ok(mobile.maxHeight < 530);
  const desktop = context.executionMenuPosition(
    { right: 900, top: 100, bottom: 150 },
    1280,
    900,
  );
  assert.equal(desktop.top, 158);
  assert.ok(desktop.left + desktop.width < 1280);
});

test("menu keyboard navigation cycles modes and Escape returns to the toggle", () => {
  const context = vm.createContext({ document: { activeElement: null } });
  const items = [0, 1, 2, 3].map(() => ({
    focus() {
      context.document.activeElement = this;
    },
  }));
  let closed = false;
  let returned = false;
  Object.assign(context, {
    executionMenuRef: { current: { querySelectorAll: () => items } },
    executionMenuToggleRef: {
      current: {
        focus: () => {
          returned = true;
        },
      },
    },
    setIsExecutionMenuOpen: (value) => {
      closed = !value;
    },
  });
  load(context, "handleExecutionMenuKeyDown");
  const key = (value) =>
    context.handleExecutionMenuKeyDown({ key: value, preventDefault() {} });
  items[0].focus();
  key("ArrowUp");
  assert.equal(context.document.activeElement, items[3]);
  key("Home");
  assert.equal(context.document.activeElement, items[0]);
  key("End");
  assert.equal(context.document.activeElement, items[3]);
  key("Escape");
  assert.ok(closed && returned);
});
