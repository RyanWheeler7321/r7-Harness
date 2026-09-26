import type {
	ExtensionAPI,
	ExtensionContext,
	Theme,
} from "@oh-my-pi/pi-coding-agent";
import {
	appendFileSync,
	closeSync,
	existsSync,
	mkdirSync,
	openSync,
	readFileSync,
	realpathSync,
	renameSync,
	statSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, normalize, parse, relative, resolve } from "node:path";

const VERSION = 1;
const MAX_CONFIG_BYTES = 64 * 1024;
const MAX_SCOPE_BYTES = 256 * 1024;
const MAX_LOG_LINE_BYTES = 768;
const MAX_LOG_LINES = 128;
const MAX_LOG_BYTES = 128 * 1024;

const MUTATING_TOOLS = new Set([
	"edit",
	"write",
	"apply_patch",
	"ast_edit",
	"eval",
	"debug",
	"launch",
	"process",
]);
const OWNED_FILE_TOOLS = new Set(["edit", "write", "apply_patch"]);
// Helpers wait on jobs with `wait` and message peers with `write agent://<id>`.
const HELPER_READ_TOOLS = new Set([
	"read",
	"grep",
	"glob",
	"ast_grep",
	"inspect_image",
	"web_search",
	"wait",
	"yield",
]);
const HELPER_WRITE_TOOLS = new Set([...HELPER_READ_TOOLS, ...OWNED_FILE_TOOLS]);
const READ_ONLY_GITHUB_OPERATIONS = new Set([
	"repo_view",
	"file_read",
	"search_issues",
	"search_prs",
	"search_code",
	"search_commits",
	"search_repos",
	"run_watch",
]);
// Input fields that name a file, also checked inside edits[] entries.
const TARGET_KEYS = ["path", "file", "file_path", "filePath", "target", "destination", "new_path", "newPath", "rename", "move"];
// Running tools get this long to finish before a steer interrupts the batch.
const STEER_GRACE_MS = 10_000;
const STEER_PROTECTED_TOOLS = new Set(["edit", "write", "apply_patch", "ast_edit", "ask"]);

export type Scope = { kind: "file" | "dir"; path: string };
export type ScopeIntent = {
	kind: "absent" | "readOnly" | "write" | "invalid";
	scopes: Scope[];
	positiveMutation: boolean;
	error?: string;
};

type Role = "idle" | "working";
type ThemeSelection = Record<Role, { theme: string; contrast: number }>;
type ThemeCatalog = {
	version: number;
	themes: Array<{ id: string; label: string; description?: string }>;
	defaultPair?: Record<Role, string>;
};
type Roots = {
	config: string;
	data: string;
	profile: string;
	runtime: string;
	log: string;
	scopes: string;
	scopeLock: string;
	selection: string;
	catalog: string;
};
type Reservation = {
	owner: string;
	scopes: Scope[];
	createdAt: string;
};
type ScopeRegistry = {
	version: number;
	reservations: Reservation[];
};
type RuntimeThemes = Record<Role, { id: string; theme: Theme }>;
type UpdateMode = "idle" | "running" | "pause";

export const FILES_SYNTAX = [
	"Use exactly one of these forms:",
	"# Files",
	"- read-only",
	"",
	"# Files",
	"- file: path/to/file",
	"- dir: path/to/directory",
].join("\n");

// MARK: paths

function homeDirectory(): string {
	return process.env.HOME?.trim() || homedir();
}

function environmentPath(value: string | undefined, fallback: string): string {
	const raw = (value?.trim() || fallback).replace(/^~(?=\/|$)/, homeDirectory());
	return normalize(isAbsolute(raw) ? raw : resolve(homeDirectory(), raw));
}

function profileSlug(): string {
	const value = process.env.R7HARNESS_PROFILE_SLUG?.trim() || "r7";
	return value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "r7";
}

function resolveRoots(): Roots {
	const home = homeDirectory();
	const config = environmentPath(process.env.R7HARNESS_HOME, join(home, ".config", "r7harness"));
	const data = environmentPath(process.env.R7HARNESS_DATA, join(home, ".local", "share", "r7harness"));
	// the launcher passes the profile path, OMP_PROFILE is only a profile name
	const profile = environmentPath(
		process.env.R7HARNESS_PROFILE || process.env.PI_CODING_AGENT_DIR,
		join(home, ".omp", "profiles", profileSlug(), "agent"),
	);
	const runtime = join(config, "runtime");
	return {
		config,
		data,
		profile,
		runtime,
		log: join(runtime, "log.jsonl"),
		scopes: join(runtime, "files.json"),
		scopeLock: join(runtime, "files.lock"),
		selection: join(profile, "themes", "selection.json"),
		catalog: join(profile, "themes", "catalog.json"),
	};
}

function isInside(parent: string, target: string): boolean {
	const difference = relative(parent, target);
	return difference === "" || (!difference.startsWith("..") && !isAbsolute(difference));
}

function directoryExists(path: string): boolean {
	try {
		return statSync(path).isDirectory();
	} catch {
		return false;
	}
}

// Resolves symlinks through the nearest existing parent so new files still get checked.
function canonicalPath(raw: unknown, cwd: string): string | undefined {
	if (typeof raw !== "string" || !raw.trim() || raw.includes("\0")) return undefined;
	const expanded = raw.trim().replace(/^~(?=\/|$)/, homeDirectory());
	const candidate = normalize(isAbsolute(expanded) ? expanded : resolve(cwd, expanded));
	if (candidate === parse(candidate).root) return undefined;

	let ancestor = candidate;
	while (!existsSync(ancestor)) {
		const parent = dirname(ancestor);
		if (parent === ancestor) return undefined;
		ancestor = parent;
	}

	try {
		const realAncestor = realpathSync(ancestor);
		const suffix = relative(ancestor, candidate);
		const canonical = suffix ? resolve(realAncestor, suffix) : realAncestor;
		return canonical === parse(canonical).root ? undefined : canonical;
	} catch {
		return undefined;
	}
}

function readJson<T>(path: string, maximum: number): T | undefined {
	try {
		if (statSync(path).size > maximum) return undefined;
		return JSON.parse(readFileSync(path, "utf8")) as T;
	} catch {
		return undefined;
	}
}

function writeTextAtomic(path: string, text: string): void {
	const temporary = `${path}.${process.pid}.tmp`;
	writeFileSync(temporary, text, { encoding: "utf8", mode: 0o600 });
	renameSync(temporary, path);
}

function writeJsonAtomic(path: string, value: unknown): void {
	writeTextAtomic(path, `${JSON.stringify(value, null, 2)}\n`);
}

function validateRoots(next: Roots): void {
	if (!directoryExists(next.config)) throw new Error("configured harness root is missing");
	if (!directoryExists(next.data)) throw new Error("configured harness data root is missing");
	if (!directoryExists(next.profile)) throw new Error("configured profile root is missing");
	if (!existsSync(next.catalog)) throw new Error("theme catalog is missing");
	mkdirSync(next.runtime, { recursive: true, mode: 0o700 });
	mkdirSync(dirname(next.selection), { recursive: true, mode: 0o700 });
}

function appendLog(roots: Roots, operation: string, status: "ok" | "blocked" | "failed", state?: string): void {
	let line = JSON.stringify({ version: VERSION, at: new Date().toISOString(), operation, status, state, pid: process.pid });
	if (Buffer.byteLength(line, "utf8") > MAX_LOG_LINE_BYTES) {
		line = JSON.stringify({ version: VERSION, operation, status, pid: process.pid });
	}
	appendFileSync(roots.log, `${line}\n`, { encoding: "utf8", mode: 0o600 });
	if (statSync(roots.log).size <= MAX_LOG_BYTES) return;
	// keep only the recent tail
	const recent = readFileSync(roots.log, "utf8")
		.split(/\r?\n/)
		.filter(Boolean)
		.slice(-MAX_LOG_LINES)
		.join("\n");
	writeTextAtomic(roots.log, recent ? `${recent}\n` : "");
}

// MARK: file scopes

function filesError(message: string): string {
	return `${message}\n\n${FILES_SYNTAX}`;
}

function removeFilesSection(text: string): string {
	const output: string[] = [];
	let inFiles = false;
	for (const line of text.split(/\r?\n/)) {
		if (/^# Files\s*$/i.test(line)) {
			inFiles = true;
			continue;
		}
		if (inFiles && (!line.trim() || /^-\s*(?:read-only|(?:file|dir)\s*:)/i.test(line.trim()))) continue;
		if (inFiles) inFiles = false;
		output.push(line);
	}
	return output.join("\n");
}

const MUTATION_VERB = "(?:edit(?:ing)?|fix(?:ing)?|repair(?:ing)?|writ(?:e|ing)|implement(?:ing)?|creat(?:e|ing)|delet(?:e|ing)|remov(?:e|ing)|renam(?:e|ing)|mov(?:e|ing)|patch(?:ing)?|refactor(?:ing)?|modif(?:y|ying)|updat(?:e|ing)|generat(?:e|ing)|chang(?:e|ing)|mutat(?:e|ing))";

export function taskLooksMutating(text: string): boolean {
	// Headings like "# Change" lay out the task, they aren't requests by themselves.
	let body = removeFilesSection(text).replace(/^# (?:Target|Change|Acceptance|Files)\s*$/gm, "");
	const phrase = `(?:${MUTATION_VERB}|make\\s+changes?)`;
	const qualifiers = "(?:(?:directly|ever|also|any|the|these|those)\\s+){0,3}";
	const target = "(?:\\s+(?:(?:any|the|these|those|source|project)\\s+)?(?:files?|code|tests?|content))?";
	const negated = new RegExp(`\\b(?:do\\s+not|don't|never|must\\s+not|should\\s+not|without)\\s+${qualifiers}${phrase}${target}(?:\\s+(?:or|and|/)\\s+${qualifiers}${phrase}${target})*`, "gi");
	body = body
		.replace(negated, " ")
		.replace(/\b(?:make|with)\s+no\s+(?:file\s+)?changes?\b/gi, " ")
		.replace(/\bno\s+(?:file\s+)?changes?\b/gi, " ");
	return new RegExp(`\\b${phrase}\\b`, "i").test(body);
}

export function classifyScopes(text: string, cwd: string): ScopeIntent {
	const positiveMutation = taskLooksMutating(text);
	const invalid = (message: string): ScopeIntent => ({ kind: "invalid", scopes: [], positiveMutation, error: filesError(message) });
	const markers = [...text.matchAll(/^# Files\s*$/gim)];
	if (markers.length === 0) return { kind: "absent", scopes: [], positiveMutation };
	if (markers.length !== 1) return invalid("A helper task needs exactly one # Files section.");

	const lines = text.slice((markers[0].index ?? 0) + markers[0][0].length).split(/\r?\n/);
	const scopes: Scope[] = [];
	const seen = new Set<string>();
	let readOnly = false;
	let sawEntry = false;
	for (const rawLine of lines) {
		const line = rawLine.trim();
		if (!line) continue;
		if (/^#{1,6}\s+/.test(line)) break;
		if (/^-\s*read-only\s*$/i.test(line)) {
			readOnly = true;
			sawEntry = true;
			continue;
		}
		const match = /^-\s*(file|dir)\s*:\s*(.+)$/i.exec(line);
		if (!match) {
			// plain text after the entries ends the section
			if (line.startsWith("-") || !sawEntry) return invalid(`Malformed # Files entry: ${line}`);
			break;
		}
		const path = canonicalPath(match[2], cwd);
		if (!path || path === canonicalPath(cwd, cwd)) return invalid(`File path is empty, unsafe, or too broad: ${match[2]}`);
		const kind = match[1].toLowerCase() as Scope["kind"];
		const key = `${kind}\0${path}`;
		if (!seen.has(key)) {
			seen.add(key);
			scopes.push({ kind, path });
		}
		sawEntry = true;
	}

	if (!sawEntry) return invalid("The # Files section is empty.");
	if (readOnly && scopes.length > 0) return invalid("read-only can't be mixed with file or dir entries.");
	return readOnly
		? { kind: "readOnly", scopes: [], positiveMutation }
		: { kind: "write", scopes, positiveMutation };
}

function scopesOverlap(left: Scope, right: Scope): boolean {
	if (left.kind === "file" && right.kind === "file") return left.path === right.path;
	if (left.kind === "dir" && right.kind === "dir") {
		return isInside(left.path, right.path) || isInside(right.path, left.path);
	}
	return left.kind === "dir" ? isInside(left.path, right.path) : isInside(right.path, left.path);
}

function scopeCovers(scope: Scope, target: string): boolean {
	return scope.kind === "file" ? scope.path === target : isInside(scope.path, target);
}

function scopeKey(scope: Scope): string {
	return `${scope.kind}\0${scope.path}`;
}

function readRegistry(roots: Roots): ScopeRegistry {
	if (!existsSync(roots.scopes)) return { version: VERSION, reservations: [] };
	const registry = readJson<ScopeRegistry>(roots.scopes, MAX_SCOPE_BYTES);
	if (!registry || registry.version !== VERSION || !Array.isArray(registry.reservations)) {
		throw new Error("file scope registry is invalid");
	}
	return registry;
}

function withScopeLock<T>(roots: Roots, operation: () => T, retry = true): T {
	let descriptor: number;
	try {
		descriptor = openSync(roots.scopeLock, "wx", 0o600);
	} catch (error) {
		const code = (error as { code?: string } | undefined)?.code;
		if (code === "EEXIST" && retry) {
			try {
				// a lock older than a minute was left behind by a dead process
				if (Date.now() - statSync(roots.scopeLock).mtimeMs > 60_000) {
					unlinkSync(roots.scopeLock);
					return withScopeLock(roots, operation, false);
				}
			} catch {
				// another process just took it
			}
		}
		throw new Error("file scope registry is busy; refusing overlapping helper work");
	}

	try {
		writeFileSync(descriptor, `${process.pid}\n`, "utf8");
		return operation();
	} finally {
		closeSync(descriptor);
		try {
			unlinkSync(roots.scopeLock);
		} catch {
			// already gone
		}
	}
}

function reserveScopes(roots: Roots, reservations: Reservation[]): void {
	if (reservations.length === 0) return;
	withScopeLock(roots, () => {
		const registry = readRegistry(roots);
		const taken = registry.reservations.flatMap(entry => entry.scopes);
		for (const reservation of reservations) {
			for (const scope of reservation.scopes) {
				if (taken.some(existing => scopesOverlap(existing, scope))) {
					throw new Error(`Helper files overlap another helper's files: ${scope.path}`);
				}
				taken.push(scope);
			}
		}
		registry.reservations.push(...reservations);
		writeJsonAtomic(roots.scopes, registry);
	});
}

function adoptScopes(roots: Roots, owner: string, expected: Scope[]): Scope[] {
	return withScopeLock(roots, () => {
		const reservation = readRegistry(roots).reservations.find(entry => entry.owner === owner);
		if (!reservation) throw new Error("helper file reservation is missing");
		const actual = new Set(reservation.scopes.map(scopeKey));
		const requested = new Set(expected.map(scopeKey));
		if (actual.size !== requested.size || [...actual].some(value => !requested.has(value))) {
			throw new Error("helper files don't match the reserved scope");
		}
		return reservation.scopes;
	});
}

function releaseScopes(roots: Roots, owners: readonly string[]): void {
	if (owners.length === 0) return;
	try {
		withScopeLock(roots, () => {
			const registry = readRegistry(roots);
			registry.reservations = registry.reservations.filter(entry => !owners.includes(entry.owner));
			writeJsonAtomic(roots.scopes, registry);
		});
	} catch {
		// leave the reservation in place rather than guess
	}
}

// MARK: commands

function commandText(input: unknown): string {
	if (!input || typeof input !== "object") return "";
	const command = (input as Record<string, unknown>).command;
	return typeof command === "string" ? command : "";
}

function commandHasShellComposition(command: string): boolean {
	return !command.trim() || command.includes("\n") || /[;&|<>`]|[$][(]/.test(command);
}

function strictlyReadOnlyCommand(command: string): boolean {
	if (commandHasShellComposition(command)) return false;
	if (/\b(?:rm|mv|cp|touch|mkdir|rmdir|chmod|chown|sed\s+-i|perl\s+-i|tee|install)\b/i.test(command)) return false;
	if (/\b(?:-delete|-exec|-execdir|--output|--fix)\b/i.test(command)) return false;
	return /^(?:git\s+(?:status|diff|log)\b|(?:rg|grep|find|ls|pwd|cat|head|tail|wc)\b|sed\s+-n\b)/i.test(command.trim());
}

function rtkCandidate(command: string): boolean {
	if (commandHasShellComposition(command) || /\b(?:-delete|-exec|-execdir|--fix)\b/i.test(command)) return false;
	return /^(?:git\s+(?:status|diff|log)\b|(?:rg|grep|find|ls)\b|(?:pytest|ruff|pyright|mypy|tsc|cargo|go|dotnet|npm|pnpm|yarn|bun|make|just)\b|python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest)\b)/i.test(command.trim());
}

function forbiddenRtkWrapper(command: string): boolean {
	return /(^|[;&|]\s*)rtk\s+(?:run|proxy)\b/i.test(command);
}

// A warning, not a block. Shown once per turn.
function heavyScanWarning(command: string): string | undefined {
	const normalized = command.replaceAll("\\", "/");
	const heavyRoot = /(?:^|[\s/'"=])(?:node_modules|\.git\/objects|\.cache|\.codex\/(?:archived_)?sessions|Library|Temp|AppData\/(?:Local|Roaming)\/[^/]*\/User Data)(?:[/\s'"]|$)/i;
	if (!heavyRoot.test(normalized) || !/\b(?:rg|grep|find|fd|du|ls)\b/i.test(normalized)) return undefined;
	return "Scanning a heavy root (caches, sessions, node_modules). Narrow the path and cap the output.";
}

// If RTK is installed, common read and test commands go through `rtk rewrite` for shorter output.
async function maybeRewriteWithRtk(pi: ExtensionAPI, command: string, cwd: string): Promise<string | undefined> {
	if (!rtkCandidate(command) || !Bun.which("rtk")) return undefined;
	try {
		const result = await pi.exec("rtk", ["rewrite", command], { timeout: 5_000, cwd });
		// 0 and 3 both mean it has a rewrite, 1 means no RTK equivalent
		if (result.code !== 0 && result.code !== 3) return undefined;
		const rewritten = String(result.stdout ?? "").trim();
		if (!rewritten.startsWith("rtk ") || rewritten.includes("\n")) return undefined;
		if (!rtkCandidate(rewritten.slice(4)) || forbiddenRtkWrapper(rewritten)) return undefined;
		return rewritten;
	} catch {
		return undefined;
	}
}

// MARK: edit targets

function unquotePath(raw: string): string | undefined {
	const value = raw.trim();
	const quote = value[0];
	if (quote !== "\"" && quote !== "'") return value || undefined;
	if (value.length < 2 || !value.endsWith(quote)) return undefined;
	return value.slice(1, -1).replace(/\\([\\"'])/g, "$1") || undefined;
}

// Targets from an apply_patch payload or hashline [path#HASH] headers, never both.
function payloadTargets(payload: string): { raw: string[]; error?: string } {
	const lines = payload.replace(/^\uFEFF/, "").split(/\r?\n/);
	const action = /^\*\*\*\s+(Add|Update|Delete) File:\s*(.*?)\s*$/i;
	const move = /^\*\*\*\s+Move to:\s*(.*?)\s*$/i;
	const hashHeader = /^\[([^#\]]+)(?:#[0-9a-f]{4})?\]\s*$/i;
	const isApply = lines.some(line => /^\*\*\*\s+(?:Add|Update|Delete|Move)\b/i.test(line));
	const isHashline = lines.some(line => hashHeader.test(line));
	if (isApply && isHashline) return { raw: [], error: "mixed apply_patch and hashline targets" };

	const raw: string[] = [];
	if (isApply) {
		let moveAllowed = false;
		for (const line of lines) {
			const header = action.exec(line);
			if (header) {
				const path = unquotePath(header[2]);
				if (!path) return { raw: [], error: `malformed ${header[1]} File target` };
				raw.push(path);
				moveAllowed = header[1].toLowerCase() === "update";
				continue;
			}
			const destination = move.exec(line);
			if (destination) {
				const path = moveAllowed ? unquotePath(destination[1]) : undefined;
				if (!path) return { raw: [], error: "Move to needs a path right after an Update File header" };
				raw.push(path);
				moveAllowed = false;
				continue;
			}
			if (/^\*\*\*\s+/.test(line) && !/^\*\*\*\s+(?:Begin Patch|End Patch|End of File)\s*$/i.test(line)) {
				return { raw: [], error: `unknown patch header: ${line.trim()}` };
			}
			if (line.trim()) moveAllowed = false;
		}
	} else if (isHashline) {
		let source = false;
		for (const line of lines) {
			const header = hashHeader.exec(line);
			if (header) {
				raw.push(header[1].trim());
				source = true;
				continue;
			}
			const trimmed = line.trim();
			if (/^MV\b/.test(trimmed)) {
				const path = source ? unquotePath(trimmed.slice(2)) : undefined;
				if (!path) return { raw: [], error: "hashline MV needs a destination after a source header" };
				raw.push(path);
			}
		}
	}
	return raw.length > 0 ? { raw } : { raw: [], error: "edit payload has no file targets" };
}

export function mutationTargets(toolName: string, input: unknown, cwd: string): { paths: string[]; error?: string } {
	const raw: string[] = [];
	let payload = typeof input === "string" ? input : "";
	if (input && typeof input === "object" && !Array.isArray(input)) {
		const record = input as Record<string, unknown>;
		const fields: Array<[string, unknown]> = TARGET_KEYS.map(key => [key, record[key]]);
		if (Array.isArray(record.paths)) record.paths.forEach((value, index) => fields.push([`paths[${index}]`, value]));
		if (Array.isArray(record.edits)) {
			record.edits.forEach((edit, index) => {
				if (!edit || typeof edit !== "object") return;
				for (const key of TARGET_KEYS) fields.push([`edits[${index}].${key}`, (edit as Record<string, unknown>)[key]]);
			});
		}
		for (const [label, value] of fields) {
			if (value === undefined) continue;
			if (typeof value !== "string" || !value.trim()) return { paths: [], error: `${label} is not a path` };
			raw.push(value);
		}
		const payloadKeys = toolName === "apply_patch" ? ["input", "_input", "patch", "content"] : ["input", "_input", "patch"];
		const payloads = payloadKeys
			.map(key => record[key])
			.filter((value): value is string => typeof value === "string" && !!value.trim());
		if (new Set(payloads).size > 1) return { paths: [], error: "edit has more than one payload" };
		payload = payloads[0] ?? "";
	} else if (typeof input !== "string") {
		return { paths: [], error: "tool input is missing" };
	}

	if (payload && (toolName === "edit" || toolName === "apply_patch")) {
		const parsed = payloadTargets(payload);
		if (parsed.error) return { paths: [], error: parsed.error };
		raw.push(...parsed.raw);
	}
	const paths: string[] = [];
	for (const value of raw) {
		const path = canonicalPath(value, cwd);
		if (!path) return { paths: [], error: `unsafe target: ${value}` };
		if (!paths.includes(path)) paths.push(path);
	}
	return paths.length > 0 ? { paths } : { paths: [], error: "tool has no recognized file target" };
}

// `write agent://<id>` is a peer message, not a file change.
function isPeerMessage(toolName: string, input: unknown): boolean {
	if (toolName !== "write" || !input || typeof input !== "object") return false;
	const path = (input as Record<string, unknown>).path;
	return typeof path === "string" && path.trim().startsWith("agent://");
}

function isUrlWrite(toolName: string, input: unknown): boolean {
	if (toolName !== "write" || !input || typeof input !== "object") return false;
	const path = (input as Record<string, unknown>).path;
	return typeof path === "string" && /^[a-z][a-z0-9+.-]*:\/\//i.test(path.trim());
}

// MARK: helpers

function ownerFromPrompt(prompt: unknown): string | undefined {
	if (typeof prompt !== "string") return undefined;
	return /^# r7Harness Owner\s*\n([^\n]+)\s*$/im.exec(prompt)?.[1]?.trim() || undefined;
}

function guardTask(text: string, intent: ScopeIntent, owner: string): string {
	const files = intent.kind === "absent" ? "\n\n# Files\n- read-only" : "";
	const scope = intent.kind === "write" ? "Edit only inside # Files." : "This helper is read-only.";
	return `${text.trimEnd()}${files}\n\n# r7Harness Owner\n${owner}\n\n${scope} Send progress or questions with write agent://Main. Do not spawn helpers or use shell, process, browser, MCP, eval, debug, or launch tools.`;
}

function githubIsReadOnly(input: unknown): boolean {
	if (!input || typeof input !== "object") return false;
	const operation = (input as Record<string, unknown>).op;
	return typeof operation === "string" && READ_ONLY_GITHUB_OPERATIONS.has(operation);
}

function requiresReadiness(toolName: string, input: unknown): boolean {
	if (MUTATING_TOOLS.has(toolName) || toolName === "task" || toolName.startsWith("mcp__")) return true;
	if (toolName === "github") return !githubIsReadOnly(input);
	if (toolName === "bash") return !strictlyReadOnlyCommand(commandText(input));
	return false;
}

function nestedCapability(toolName: string): boolean {
	return toolName === "bash"
		|| toolName === "task"
		|| toolName === "github"
		|| toolName === "process"
		|| toolName === "eval"
		|| toolName === "debug"
		|| toolName === "launch"
		|| toolName === "browser"
		|| toolName.startsWith("mcp__");
}

function transformedTasks(
	roots: Roots,
	instanceId: string,
	input: Record<string, unknown>,
	cwd: string,
	callId: string | undefined,
): { input: Record<string, unknown>; owners: string[] } {
	const items = Array.isArray(input.tasks) ? input.tasks : [input];
	const reservations: Reservation[] = [];
	const revised = items.map((item, index) => {
		if (!item || typeof item !== "object") throw new Error("helper task input is invalid");
		const record = item as Record<string, unknown>;
		const text = typeof record.task === "string" ? record.task : "";
		const intent = classifyScopes(text, cwd);
		if (intent.kind === "invalid") throw new Error(intent.error ?? filesError("Invalid helper files."));
		if (intent.kind === "absent" && intent.positiveMutation) {
			throw new Error(filesError(`Writing task ${index + 1} needs a # Files section with its exact files or directories.`));
		}
		if (intent.kind === "write" && !callId) throw new Error("A writing helper needs a stable task call id.");
		const owner = `${instanceId}:${callId ?? "read"}:${index}`;
		if (intent.kind === "write") {
			reservations.push({ owner, scopes: intent.scopes, createdAt: new Date().toISOString() });
		}
		return { ...record, task: guardTask(text, intent, owner) };
	});
	reserveScopes(roots, reservations);
	return {
		input: Array.isArray(input.tasks) ? { ...input, tasks: revised } : revised[0],
		owners: reservations.map(entry => entry.owner),
	};
}

// MARK: update checkpoint
// "update" mid-task gets answered in its own text-only message (thinking doesn't count),
// then the task picks back up. The note is added the same way on every provider call so the cache holds.

const UPDATE_REQUEST = /^[^\w/]*(?:update\s*(?:[.!?,:;]|$)|any updates?\b|give me an update\b|status update\b|how(?: are)? we doing\b)|\bupdate (?:me|pause)\b/i;
const UPDATE_PAUSE = /\bupdate pause\b/i;
const UPDATE_MIN_CHARS = 40;
const UPDATE_BLOCK_CAP = 2;
const UPDATE_NOTE_TAG = "[r7Harness: ";
const UPDATE_NOTES: Record<UpdateMode, string> = {
	idle: `${UPDATE_NOTE_TAG}the user asked for an update. Answer in visible reply text before any tool call; thinking doesn't count.]`,
	running: `${UPDATE_NOTE_TAG}the user asked for an update mid-task. Reply with only the update as visible text, in a message with no tool calls. The task resumes right after.]`,
	pause: `${UPDATE_NOTE_TAG}the user asked for an update and a pause. Reply with only the update as visible text, with no tool calls, then stop.]`,
};
const UPDATE_OWED = "Update owed: the user asked for an update. Send it now as visible reply text in a message with no tool calls. This tool call did not run.";
const UPDATE_RESUME = "Update delivered. Continue the active task from where you left off without repeating the update. If a background job you need is still running, use the wait tool instead of ending the turn.";

function userText(message: any): string {
	if (typeof message?.content === "string") return message.content;
	if (!Array.isArray(message?.content)) return "";
	return message.content.filter((part: any) => part?.type === "text").map((part: any) => part.text).join("\n");
}

export function isUpdateRequest(text: string): boolean {
	return UPDATE_REQUEST.test(text.trim());
}

// A steer, or a message right after tool results, arrived while work was running.
function updateMode(messages: any[], index: number): UpdateMode {
	if (UPDATE_PAUSE.test(userText(messages[index]))) return "pause";
	return messages[index]?.steering === true || messages[index - 1]?.role === "toolResult" ? "running" : "idle";
}

export function withUpdateNotes(messages: any[]): any[] | undefined {
	let changed: any[] | undefined;
	messages.forEach((message, index) => {
		if (message?.role !== "user") return;
		const text = userText(message);
		if (!isUpdateRequest(text) || text.includes(UPDATE_NOTE_TAG)) return;
		const note = UPDATE_NOTES[updateMode(messages, index)];
		const content = typeof message.content === "string"
			? `${message.content}\n\n${note}`
			: [...message.content, { type: "text", text: note }];
		changed ??= messages.slice();
		changed[index] = { ...message, content };
	});
	return changed;
}

// The newest user message, when nothing has replied to it yet.
function deliveredUpdate(messages: any[]): { key: string; mode: UpdateMode } | undefined {
	for (let index = messages.length - 1; index >= 0; index--) {
		const message = messages[index];
		if (message?.role === "assistant") return undefined;
		if (message?.role !== "user") continue;
		const text = userText(message);
		return isUpdateRequest(text) ? { key: `${message.timestamp ?? index}:${text}`, mode: updateMode(messages, index) } : undefined;
	}
	return undefined;
}

function visibleTextLength(message: any): number {
	if (message?.role !== "assistant" || !Array.isArray(message.content)) return 0;
	let length = 0;
	for (const block of message.content) {
		if (block?.type === "text" && typeof block.text === "string") length += block.text.replace(/\s+/g, "").length;
	}
	return length;
}

function isTextOnlyUpdate(message: any): boolean {
	const toolCalls = Array.isArray(message?.content) && message.content.some((block: any) => block?.type === "toolCall");
	return visibleTextLength(message) >= UPDATE_MIN_CHARS && !toolCalls;
}

function lastAssistant(messages: any[] | undefined): any {
	return messages?.findLast?.((message: any) => message?.role === "assistant");
}

function notify(ctx: ExtensionContext, message: string, level: "info" | "warning" | "error"): void {
	if (ctx.hasUI) ctx.ui.notify(message, level);
}

export default function r7HarnessExtension(pi: ExtensionAPI): void {
	pi.setLabel("r7Harness");

	// OMP can load this module once for the main session and its helpers, so all state lives here.
	let roots: Roots | undefined;
	let ready = false;
	let initError = "r7Harness has not initialized";
	let helperSession = false;
	let adoptedOwner = "";
	let adoptedScopes: Scope[] = [];
	let runtimeThemes: RuntimeThemes | undefined;
	let activeThemeRole: Role | undefined;
	let agentWorking = false;
	let warnedHeavyScan = false;
	const instanceId = `${process.pid}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
	const reservationsByCall = new Map<string, string[]>();

	function recordOrFail(operation: string, status: "ok" | "blocked" | "failed", state?: string): boolean {
		try {
			if (!roots) throw new Error("log root is unavailable");
			appendLog(roots, operation, status, state);
			return true;
		} catch (error) {
			ready = false;
			initError = error instanceof Error ? error.message : String(error);
			pi.logger.error("r7Harness log failure", { error: initError, operation });
			return false;
		}
	}

	// MARK: themes

	async function configureThemes(ctx: ExtensionContext): Promise<boolean> {
		if (!roots || ctx.mode !== "tui" || !ctx.hasUI) return false;
		const catalog = readJson<ThemeCatalog>(roots.catalog, MAX_CONFIG_BYTES);
		if (!catalog || !Array.isArray(catalog.themes)) throw new Error("theme catalog is invalid");
		const fallback: ThemeSelection = {
			idle: { theme: catalog.defaultPair?.idle ?? "", contrast: 100 },
			working: { theme: catalog.defaultPair?.working ?? "", contrast: 100 },
		};
		const selection = readJson<ThemeSelection>(roots.selection, MAX_CONFIG_BYTES) ?? fallback;
		const ids = new Set(catalog.themes.map(theme => theme.id));
		if (!ids.has(selection.idle?.theme) || !ids.has(selection.working?.theme)) {
			throw new Error("theme selection is not in the catalog");
		}
		const idle = await ctx.ui.getTheme(selection.idle.theme);
		const working = await ctx.ui.getTheme(selection.working.theme);
		if (!idle || !working) throw new Error("configured theme is not registered with OMP");
		runtimeThemes = {
			idle: { id: selection.idle.theme, theme: idle },
			working: { id: selection.working.theme, theme: working },
		};
		return true;
	}

	async function activateTheme(ctx: ExtensionContext, role: Role, force = false): Promise<boolean> {
		const target = runtimeThemes?.[role];
		if (!target || (!force && activeThemeRole === role)) return !!target;
		const result = await ctx.ui.setTheme(target.theme);
		if (!result.success) return false;
		activeThemeRole = role;
		return true;
	}

	function selectedThemes(): ThemeSelection | undefined {
		if (!roots) return undefined;
		return readJson<ThemeSelection>(roots.selection, MAX_CONFIG_BYTES);
	}

	async function setThemePair(ctx: ExtensionContext, next: ThemeSelection): Promise<void> {
		if (!roots) throw new Error("theme root is unavailable");
		const previous = selectedThemes();
		writeJsonAtomic(roots.selection, { version: VERSION, ...next });
		try {
			await configureThemes(ctx);
			if (!(await activateTheme(ctx, agentWorking ? "working" : "idle", true))) {
				throw new Error("theme activation failed");
			}
		} catch (error) {
			if (previous) {
				writeJsonAtomic(roots.selection, previous);
				await configureThemes(ctx);
				await activateTheme(ctx, agentWorking ? "working" : "idle", true);
			}
			throw error;
		}
	}

	// MARK: steer
	// A message typed mid-turn normally waits for the whole tool batch. Running tools get
	// STEER_GRACE_MS to finish, then the batch is interrupted so the message lands now.
	// A reply that's still being written finishes, and edits and questions are never cut off.

	const runningTools = new Map<string, string>();
	let steer: { deadline: number; timer: ReturnType<typeof setInterval>; ctx: ExtensionContext } | undefined;
	const stopSteer = () => {
		if (steer) clearInterval(steer.timer);
		steer = undefined;
	};
	const checkSteer = () => {
		const current = steer;
		if (!current) return;
		if (!ready || current.ctx.isIdle()) return stopSteer();
		if (Date.now() < current.deadline) return;
		if (!current.ctx.hasPendingMessages()) return stopSteer();
		const running = [...runningTools.values()];
		if (running.length === 0 || running.some(name => STEER_PROTECTED_TOOLS.has(name))) return;
		stopSteer();
		current.ctx.abort();
	};
	const armSteer = (text: string, ctx: ExtensionContext) => {
		if (!ready || helperSession || steer || ctx.isIdle()) return;
		const trimmed = text.trim();
		if (!trimmed || trimmed.startsWith("/")) return;
		const timer = setInterval(checkSteer, 250);
		timer.unref?.();
		steer = { deadline: Date.now() + STEER_GRACE_MS, timer, ctx };
	};

	// MARK: update gate
	// Once an update request reaches the model, tools wait until a reply carries visible text.

	let updateOwed = false;
	// A mid-task update answered on its own ends the loop, so the task gets resumed once.
	let updateResume = false;
	let updateBlocks = 0;
	let assistantSeq = 0;
	let updateAfterSeq = 0;
	let blockedSeq = -1;
	let assistantChars = 0;
	let armedUpdateKey = "";
	// only replies that started after the request reached the model count
	const answering = () => assistantSeq > updateAfterSeq;
	const requestUpdate = (mode: UpdateMode) => {
		updateOwed = true;
		updateResume = mode === "running";
		updateBlocks = 0;
		updateAfterSeq = assistantSeq;
	};
	const settleUpdate = (chars: number) => {
		if (updateOwed && answering() && chars >= UPDATE_MIN_CHARS) updateOwed = false;
	};
	const updateGate = (ctx: ExtensionContext): { block: true; reason: string } | undefined => {
		if (!updateOwed || helperSession || !answering()) return undefined;
		settleUpdate(assistantChars);
		if (!updateOwed) return undefined;
		if (blockedSeq !== assistantSeq) {
			if (updateBlocks >= UPDATE_BLOCK_CAP) {
				updateOwed = false;
				notify(ctx, "Update gate gave up; the update still wasn't written as visible text.", "warning");
				return undefined;
			}
			updateBlocks++;
			blockedSeq = assistantSeq;
		}
		return { block: true, reason: updateResume ? `${UPDATE_OWED} The task resumes right after that message.` : UPDATE_OWED };
	};

	// MARK: events

	pi.on("session_start", async (_event, ctx) => {
		ready = false;
		helperSession = false;
		adoptedOwner = "";
		adoptedScopes = [];
		agentWorking = false;
		activeThemeRole = undefined;
		runtimeThemes = undefined;
		updateOwed = false;
		updateResume = false;
		try {
			const next = resolveRoots();
			validateRoots(next);
			roots = next;
			appendLog(next, "ready", "ok", "idle");
			ready = true;
			initError = "";
			try {
				if (await configureThemes(ctx)) await activateTheme(ctx, "idle", true);
			} catch (error) {
				pi.logger.warn("r7Harness themes unavailable", { error: String(error) });
			}
		} catch (error) {
			initError = error instanceof Error ? error.message : String(error);
			pi.logger.error("r7Harness initialization failed", { error: initError });
			notify(ctx, `r7Harness safeguards are unavailable: ${initError}`, "error");
		}
	});

	pi.on("before_agent_start", async (event, ctx) => {
		if (!ready) return;
		warnedHeavyScan = false;
		const disable = (message: string) => {
			ready = false;
			initError = message;
			return { systemPrompt: [...event.systemPrompt, `MUTATION DISABLED: ${initError}`] };
		};
		const owner = ownerFromPrompt(event.prompt);
		if (owner) {
			helperSession = true;
			const intent = classifyScopes(String(event.prompt ?? ""), ctx.cwd);
			if (intent.kind !== "readOnly" && intent.kind !== "write") {
				return disable(intent.error ?? "helper # Files section is invalid");
			}
			if (intent.kind === "write" && !adoptedOwner) {
				if (!roots) return disable("file scope root is unavailable");
				try {
					adoptedScopes = adoptScopes(roots, owner, intent.scopes);
					adoptedOwner = owner;
				} catch (error) {
					return disable(error instanceof Error ? error.message : String(error));
				}
			}
		}
		agentWorking = true;
		await activateTheme(ctx, "working");
		if (!recordOrFail("attention", "ok", "active")) return disable(initError);
	});

	pi.on("agent_start", async () => {
		runningTools.clear();
	});

	pi.on("agent_end", async (event, ctx) => {
		// OMP can skip session_stop while a background job is pending, so resume the task here too.
		if (updateResume && !helperSession) {
			updateResume = false;
			if (isTextOnlyUpdate(lastAssistant(event.messages))) {
				pi.sendMessage(
					{ customType: "r7harness-update-resume", content: UPDATE_RESUME, display: false },
					{ triggerTurn: true, deliverAs: "nextTurn" },
				);
			}
		}
		if (event.willContinue) return;
		stopSteer();
		// a finished helper gives its files back
		if (helperSession && adoptedOwner && roots) {
			releaseScopes(roots, [adoptedOwner]);
			adoptedOwner = "";
			adoptedScopes = [];
		}
		if (!ready) return;
		agentWorking = false;
		await activateTheme(ctx, "idle");
		recordOrFail("attention", "ok", "waiting");
	});

	pi.on("tool_execution_start", async event => {
		runningTools.set(event.toolCallId, event.toolName);
	});

	pi.on("tool_execution_end", async event => {
		runningTools.delete(event.toolCallId);
		checkSteer();
	});

	pi.on("context", async event => {
		if (helperSession) return;
		const delivered = deliveredUpdate(event.messages);
		if (delivered && delivered.key !== armedUpdateKey) {
			armedUpdateKey = delivered.key;
			requestUpdate(delivered.mode);
		}
		const messages = withUpdateNotes(event.messages);
		return messages ? { messages } : undefined;
	});

	pi.on("message_start", async event => {
		if ((event.message as any)?.role !== "assistant") return;
		assistantSeq++;
		assistantChars = 0;
	});

	pi.on("message_update", async event => {
		assistantChars = visibleTextLength(event.message);
	});

	pi.on("message_end", async event => {
		if ((event.message as any)?.role !== "assistant") return;
		assistantChars = visibleTextLength(event.message);
		settleUpdate(assistantChars);
	});

	pi.on("session_stop", async event => {
		if (helperSession || !answering()) return;
		if (updateOwed) {
			settleUpdate(visibleTextLength(event.last_assistant_message));
			if (updateOwed) {
				updateOwed = false;
				return {
					continue: true,
					additionalContext: "The user asked for an update and it wasn't written as visible reply text. Write it now in a message with no tool calls.",
				};
			}
		}
		if (!updateResume) return;
		updateResume = false;
		// only the update message itself ended the loop, a normal finish is left alone
		if (!isTextOnlyUpdate(event.last_assistant_message)) return;
		return { continue: true, additionalContext: UPDATE_RESUME };
	});

	pi.on("input", async (event, ctx) => {
		if (event.source === "interactive") armSteer(event.text, ctx);
	});

	pi.on("tool_call", async (event, ctx) => {
		const owed = updateGate(ctx);
		if (owed) return owed;
		// still calling tools after the update settled, so the task carried on by itself
		if (!updateOwed && !helperSession) updateResume = false;

		const input = (event.input ?? {}) as Record<string, unknown>;
		const peerMessage = isPeerMessage(event.toolName, input);
		const command = event.toolName === "bash" ? commandText(input) : "";
		if (command && forbiddenRtkWrapper(command)) {
			recordOrFail("safeguard", "blocked");
			return { block: true, reason: "rtk run and rtk proxy are blocked because they hide the real command from safeguards." };
		}
		const scanWarning = command ? heavyScanWarning(command) : undefined;
		if (scanWarning && !warnedHeavyScan) {
			warnedHeavyScan = true;
			if (ctx.hasUI) ctx.ui.notify(scanWarning, "warning");
			else pi.logger.warn(scanWarning);
		}

		if (helperSession && !peerMessage) {
			if (isUrlWrite(event.toolName, input)) {
				return { block: true, reason: "Helpers can only write agent:// messages; the parent owns proc:// and other internal URLs." };
			}
			if (nestedCapability(event.toolName) || !HELPER_WRITE_TOOLS.has(event.toolName)) {
				return { block: true, reason: "Helpers can't use nested capabilities; the parent owns shell, process, browser, MCP, and external actions." };
			}
			if (OWNED_FILE_TOOLS.has(event.toolName)) {
				if (!adoptedOwner || adoptedScopes.length === 0) {
					return { block: true, reason: "This helper has no files to edit. The parent needs to send a new task with a # Files section." };
				}
				const targets = mutationTargets(event.toolName, event.input, ctx.cwd);
				if (targets.error || targets.paths.length === 0) {
					return { block: true, reason: `Edit target is invalid: ${targets.error ?? "none"}` };
				}
				if (!targets.paths.every(target => adoptedScopes.some(scope => scopeCovers(scope, target)))) {
					return { block: true, reason: "Edit target is outside this helper's # Files." };
				}
			}
		}

		if (!ready && !peerMessage && requiresReadiness(event.toolName, input)) {
			recordOrFail("mutation-gate", "blocked");
			return { block: true, reason: `r7Harness is not ready; mutation is blocked. ${initError}` };
		}

		if (event.toolName === "task") {
			if (helperSession) return { block: true, reason: "Helpers can't spawn nested helpers." };
			if (!roots) return { block: true, reason: `r7Harness is not ready. ${initError}` };
			try {
				const transformed = transformedTasks(roots, instanceId, input, ctx.cwd, event.toolCallId);
				if (event.toolCallId && transformed.owners.length > 0) {
					reservationsByCall.set(event.toolCallId, transformed.owners);
				}
				return { input: transformed.input };
			} catch (error) {
				recordOrFail("files", "blocked");
				return { block: true, reason: error instanceof Error ? error.message : String(error) };
			}
		}

		if (command && ready && roots) {
			const rewritten = await maybeRewriteWithRtk(pi, command, ctx.cwd);
			if (rewritten) return { input: { ...input, command: rewritten } };
		}
	});

	pi.on("tool_result", async event => {
		if (event.toolName !== "task" || !event.toolCallId) return;
		const owners = reservationsByCall.get(event.toolCallId) ?? [];
		reservationsByCall.delete(event.toolCallId);
		if (event.isError && roots) releaseScopes(roots, owners);
	});

	pi.on("session_shutdown", async (_event, ctx) => {
		stopSteer();
		agentWorking = false;
		await activateTheme(ctx, "idle", true);
		if (adoptedOwner && roots) releaseScopes(roots, [adoptedOwner]);
		adoptedOwner = "";
		adoptedScopes = [];
		recordOrFail("shutdown", "ok", "idle");
		ready = false;
	});

	pi.registerCommand("theme", {
		description: "Show or set the local idle and working theme pair",
		handler: async (args, ctx) => {
			if (!ready) {
				notify(ctx, `Theme changes are unavailable: ${initError}`, "error");
				return;
			}
			const words = args.trim().split(/\s+/).filter(Boolean);
			const current = selectedThemes();
			if (words.length === 0 || words[0] === "status") {
				if (!current) {
					notify(ctx, "No local theme selection is available.", "warning");
					return;
				}
				notify(ctx, `Idle: ${current.idle.theme} | Working: ${current.working.theme}`, "info");
				return;
			}

			const next: ThemeSelection = current
				? {
					idle: { ...current.idle },
					working: { ...current.working },
				}
				: {
					idle: { theme: "rose-signal", contrast: 100 },
					working: { theme: "crimson-voltage", contrast: 100 },
				};
			if ((words[0] === "idle" || words[0] === "working") && words.length === 2) {
				next[words[0]].theme = words[1];
			} else if (words.length === 2) {
				next.idle.theme = words[0];
				next.working.theme = words[1];
			} else {
				notify(ctx, "Usage: /theme status | /theme idle <id> | /theme working <id> | /theme <idle-id> <working-id>", "warning");
				return;
			}

			try {
				await setThemePair(ctx, next);
				notify(ctx, `Theme pair applied: ${next.idle.theme} / ${next.working.theme}`, "info");
			} catch (error) {
				notify(ctx, `Theme pair was not changed: ${error instanceof Error ? error.message : String(error)}`, "error");
			}
		},
	});
}
