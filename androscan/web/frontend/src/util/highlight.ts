/**
 * Zero-dependency Java / Kotlin syntax highlighter for the Code Browser.
 *
 * Trade-offs: this is a regex-tokenizer, not a full lexer, so it deliberately
 * avoids edge cases (template strings, nested generics, annotations with
 * arguments). It is fast, stable, and produces predictable colour classes
 * so the surrounding pane controls the theme via CSS.
 *
 * The output is a structured list of tokens that the React component can
 * render either as plain spans or with extra emphasis (e.g. method-body bold).
 */

export type TokenKind =
  | "text"
  | "keyword"
  | "type"
  | "string"
  | "number"
  | "comment"
  | "annotation"
  | "operator"
  | "punct"
  | "func";

export type Token = { kind: TokenKind; text: string };

const JAVA_KEYWORDS = new Set([
  "abstract","assert","boolean","break","byte","case","catch","char","class","const",
  "continue","default","do","double","else","enum","extends","final","finally","float",
  "for","goto","if","implements","import","instanceof","int","interface","long","native",
  "new","null","package","private","protected","public","return","short","static",
  "strictfp","super","switch","synchronized","this","throw","throws","transient","try",
  "void","volatile","while","true","false","yield","record","sealed","permits","var",
]);

const KOTLIN_KEYWORDS = new Set([
  "as","break","class","continue","do","else","false","for","fun","if","in","interface",
  "is","null","object","package","return","super","this","throw","true","try","typealias",
  "val","var","when","while","by","catch","constructor","delegate","dynamic","field",
  "file","finally","get","import","init","param","property","receiver","set","setparam",
  "where","actual","abstract","annotation","companion","const","crossinline","data","enum",
  "expect","external","final","infix","inline","inner","internal","lateinit","noinline",
  "open","operator","out","override","private","protected","public","reified","sealed",
  "suspend","tailrec","vararg",
]);

const KEYWORDS = new Set<string>([...JAVA_KEYWORDS, ...KOTLIN_KEYWORDS]);

// Capitalised identifiers tend to be types in both Java and Kotlin.
const TYPE_LIKE = /^[A-Z][A-Za-z0-9_]*$/;

// Single-pass tokenizer. Order matters: comments and strings before idents.
export function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  const n = src.length;

  const push = (kind: TokenKind, text: string) => {
    if (!text) return;
    if (out.length && out[out.length - 1].kind === kind) {
      out[out.length - 1].text += text;
    } else {
      out.push({ kind, text });
    }
  };

  while (i < n) {
    const c = src[i];

    // Line comment
    if (c === "/" && src[i + 1] === "/") {
      const end = src.indexOf("\n", i);
      const stop = end === -1 ? n : end;
      push("comment", src.slice(i, stop));
      i = stop;
      continue;
    }
    // Block comment
    if (c === "/" && src[i + 1] === "*") {
      const end = src.indexOf("*/", i + 2);
      const stop = end === -1 ? n : end + 2;
      push("comment", src.slice(i, stop));
      i = stop;
      continue;
    }
    // Strings (double, single, triple)
    if (c === '"' || c === "'") {
      // Triple-quoted Kotlin raw string
      if (c === '"' && src[i + 1] === '"' && src[i + 2] === '"') {
        const end = src.indexOf('"""', i + 3);
        const stop = end === -1 ? n : end + 3;
        push("string", src.slice(i, stop));
        i = stop;
        continue;
      }
      let j = i + 1;
      while (j < n) {
        const cj = src[j];
        if (cj === "\\" && j + 1 < n) { j += 2; continue; }
        if (cj === c) { j += 1; break; }
        if (cj === "\n") break;
        j += 1;
      }
      push("string", src.slice(i, j));
      i = j;
      continue;
    }
    // Annotations: @Identifier
    if (c === "@") {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_.]/.test(src[j])) j += 1;
      push("annotation", src.slice(i, j));
      i = j;
      continue;
    }
    // Numbers
    if (/[0-9]/.test(c)) {
      let j = i + 1;
      while (j < n && /[0-9._a-fA-FxXLlfF]/.test(src[j])) j += 1;
      push("number", src.slice(i, j));
      i = j;
      continue;
    }
    // Identifiers / keywords / type-like / func-call
    if (/[A-Za-z_$]/.test(c)) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_$]/.test(src[j])) j += 1;
      const word = src.slice(i, j);
      // Look ahead for '(' to mark function calls.
      let k = j;
      while (k < n && src[k] === " ") k += 1;
      if (KEYWORDS.has(word)) push("keyword", word);
      else if (TYPE_LIKE.test(word)) push("type", word);
      else if (src[k] === "(") push("func", word);
      else push("text", word);
      i = j;
      continue;
    }
    // Punctuation / operators (very coarse; just colour brackets and ;,.)
    if ("{}()[]".includes(c)) { push("punct", c); i += 1; continue; }
    if (";,.".includes(c)) { push("punct", c); i += 1; continue; }
    if ("=+-*/%<>!&|^~?:".includes(c)) { push("operator", c); i += 1; continue; }

    push("text", c);
    i += 1;
  }
  return out;
}

/**
 * Find the byte range (start, endExclusive) of the body of ``methodName``
 * inside ``src`` by walking braces from the first matching declaration.
 * Returns null if not found or unbalanced.
 *
 * The matcher is intentionally forgiving: it looks for ``<name>(`` followed
 * by the next ``{`` on the same logical declaration. This is enough for
 * jadx-style Java output where the method header doesn't contain inline
 * lambdas before the body.
 */
export function findMethodBodyRange(
  src: string,
  methodName: string,
): { start: number; end: number } | null {
  if (!methodName) return null;
  const safe = methodName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`\\b${safe}\\s*\\(`, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    // Walk forward to the first '{' after the matching ')'.
    let i = m.index;
    // Skip past matching parens of the parameter list.
    let parens = 0;
    let inStr: string | null = null;
    while (i < src.length) {
      const c = src[i];
      if (inStr) {
        if (c === "\\") { i += 2; continue; }
        if (c === inStr) inStr = null;
      } else {
        if (c === '"' || c === "'") inStr = c;
        else if (c === "(") parens += 1;
        else if (c === ")") {
          parens -= 1;
          if (parens === 0) { i += 1; break; }
        }
      }
      i += 1;
    }
    // Skip throws clause / whitespace until '{' or ';'.
    while (i < src.length && src[i] !== "{" && src[i] !== ";" && src[i] !== "\n") i += 1;
    if (src[i] !== "{") continue; // abstract or interface decl
    const start = i;
    // Walk braces.
    let depth = 0;
    let j = start;
    inStr = null;
    while (j < src.length) {
      const c = src[j];
      if (inStr) {
        if (c === "\\") { j += 2; continue; }
        if (c === inStr) inStr = null;
      } else if (c === "/" && src[j + 1] === "/") {
        const nl = src.indexOf("\n", j);
        j = nl === -1 ? src.length : nl;
        continue;
      } else if (c === "/" && src[j + 1] === "*") {
        const e = src.indexOf("*/", j + 2);
        j = e === -1 ? src.length : e + 2;
        continue;
      } else if (c === '"' || c === "'") {
        inStr = c;
      } else if (c === "{") depth += 1;
      else if (c === "}") {
        depth -= 1;
        if (depth === 0) { return { start, end: j + 1 }; }
      }
      j += 1;
    }
  }
  return null;
}
