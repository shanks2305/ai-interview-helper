(function (root) {
  const KEYWORDS = {
    python:
      "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield",
    javascript:
      "async await break case catch class const continue debugger default delete do else export extends false finally for function if import in instanceof let new null of return static super switch this throw true try typeof var void while with yield",
    typescript:
      "abstract any as async await boolean break case catch class const continue debugger declare default delete do else enum export extends false finally for from function if implements import in infer instanceof interface keyof let namespace never new null number of private protected public readonly return static string super switch this throw true try type typeof undefined var void while with yield",
    go: "break case chan const continue default defer else fallthrough false for func go goto if import interface map nil package range return select struct switch true type var",
    java: "abstract assert boolean break byte case catch char class const continue default do double else enum extends false final finally float for goto if implements import instanceof int interface long native new null package private protected public return short static strictfp super switch synchronized this throw throws transient true try void volatile while",
    rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
    sql: "add all alter and as asc between by case check column constraint create default delete desc distinct drop else exists false from full group having in index inner insert into is join key left like limit not null on or order outer primary references right select set table then true union update values when where",
    bash: "alias break case cd continue do done elif else esac exit export fi for function if in local return then until while",
    c: "auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while",
    cpp: "auto bool break case catch char class const const_cast continue default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int long mutable namespace new nullptr operator private protected public register reinterpret_cast return short signed sizeof static static_cast struct switch template this throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while",
    ruby: "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield",
    json: "false null true",
  };

  const LANG_ALIAS = {
    py: "python",
    python: "python",
    js: "javascript",
    javascript: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    ts: "typescript",
    typescript: "typescript",
    tsx: "typescript",
    go: "go",
    golang: "go",
    java: "java",
    rust: "rust",
    rs: "rust",
    sql: "sql",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    shell: "bash",
    c: "c",
    h: "c",
    cpp: "cpp",
    cc: "cpp",
    cxx: "cpp",
    "c++": "cpp",
    hpp: "cpp",
    rb: "ruby",
    ruby: "ruby",
    json: "json",
  };

  const HASH_COMMENT = new Set(["python", "bash", "ruby"]);

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function normalizeLang(lang) {
    return LANG_ALIAS[String(lang || "").trim().toLowerCase()] || "";
  }

  function appendText(parent, text) {
    if (text) {
      parent.append(text);
    }
  }

  function appendToken(parent, text, className) {
    if (!text) {
      return;
    }
    const span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    parent.append(span);
  }

  function highlightCode(code, lang) {
    const frag = document.createDocumentFragment();
    const name = normalizeLang(lang);
    const keywords = (KEYWORDS[name] || "").split(" ").filter(Boolean);
    const parts = [
      name && HASH_COMMENT.has(name) ? "#[^\\n]*" : "",
      name && !HASH_COMMENT.has(name) && name !== "json" ? "//[^\\n]*|/\\*[\\s\\S]*?\\*/" : "",
      name === "python" ? "'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"" : "",
      "'(?:\\\\.|[^'\\\\])*'|\"(?:\\\\.|[^\"\\\\])*\"",
      name === "javascript" || name === "typescript" || name === "bash" ? "`(?:\\\\.|[^`\\\\])*`" : "",
      keywords.length ? `\\b(?:${keywords.map(escapeRegExp).join("|")})\\b` : "",
      "\\b\\d+(?:\\.\\d+)?\\b",
    ].filter(Boolean);
    const pattern = new RegExp(`(${parts.join("|")})`, "g");
    let last = 0;
    let match = pattern.exec(code);
    while (match) {
      appendText(frag, code.slice(last, match.index));
      const token = match[0];
      let kind = "md-num";
      if (token.startsWith("#") || token.startsWith("//") || token.startsWith("/*")) {
        kind = "md-cmt";
      } else if (token.startsWith("'") || token.startsWith('"') || token.startsWith("`")) {
        kind = "md-str";
      } else if (keywords.includes(token)) {
        kind = "md-kw";
      }
      appendToken(frag, token, kind);
      last = match.index + token.length;
      match = pattern.exec(code);
    }
    appendText(frag, code.slice(last));
    return frag;
  }

  function matchFence(line) {
    const match = /^(```|~~~)([^\n`~]*)$/.exec(line);
    if (!match) {
      return null;
    }
    return { mark: match[1], lang: match[2].trim().split(/\s+/)[0] || "" };
  }

  function isUl(line) {
    return /^[-*+] /.test(line);
  }

  function isOl(line) {
    return /^\d+[.)] /.test(line);
  }

  function isTableSep(line) {
    return /^\|?[\s:|-]+\|[\s:|-]*\|?$/.test(line.trim()) && /---/.test(line);
  }

  function isTableRow(line) {
    return line.includes("|");
  }

  function parseTableRow(line) {
    const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split("|").map((cell) => cell.trim());
  }

  function parseBlocks(source) {
    const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let i = 0;

    while (i < lines.length) {
      if (!lines[i].trim()) {
        i += 1;
        continue;
      }

      const fence = matchFence(lines[i]);
      if (fence) {
        i += 1;
        const body = [];
        while (i < lines.length && !matchFence(lines[i])) {
          body.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) {
          i += 1;
        }
        blocks.push({ type: "code", lang: fence.lang, text: body.join("\n") });
        continue;
      }

      const heading = /^(#{1,3})\s+(.+)$/.exec(lines[i]);
      if (heading) {
        blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
        i += 1;
        continue;
      }

      if (isUl(lines[i]) || isOl(lines[i])) {
        const ordered = isOl(lines[i]);
        const items = [];
        while (i < lines.length && (ordered ? isOl(lines[i]) : isUl(lines[i]))) {
          items.push(lines[i].replace(ordered ? /^\d+[.)] / : /^[-*+] /, ""));
          i += 1;
        }
        blocks.push({ type: "list", ordered, items });
        continue;
      }

      if (i + 1 < lines.length && isTableRow(lines[i]) && isTableSep(lines[i + 1])) {
        const rows = [parseTableRow(lines[i])];
        i += 2;
        while (i < lines.length && isTableRow(lines[i]) && !isTableSep(lines[i]) && !matchFence(lines[i])) {
          rows.push(parseTableRow(lines[i]));
          i += 1;
        }
        blocks.push({ type: "table", rows });
        continue;
      }

      const para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !matchFence(lines[i]) &&
        !/^(#{1,3})\s+/.test(lines[i]) &&
        !isUl(lines[i]) &&
        !isOl(lines[i])
      ) {
        para.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "paragraph", text: para.join(" ") });
    }

    return blocks;
  }

  function appendInline(parent, text) {
    const chunks = String(text).split(/(`[^`]+`)/);
    for (const chunk of chunks) {
      if (chunk.startsWith("`") && chunk.endsWith("`") && chunk.length >= 2) {
        const code = document.createElement("code");
        code.className = "md-code";
        code.textContent = chunk.slice(1, -1);
        parent.append(code);
        continue;
      }
      const pattern = /\*\*([^*]+)\*\*|\*([^*]+)\*/g;
      let last = 0;
      let match = pattern.exec(chunk);
      while (match) {
        appendText(parent, chunk.slice(last, match.index));
        const el = document.createElement(match[1] != null ? "strong" : "em");
        el.textContent = match[1] ?? match[2];
        parent.append(el);
        last = match.index + match[0].length;
        match = pattern.exec(chunk);
      }
      appendText(parent, chunk.slice(last));
    }
  }

  function renderBlock(block) {
    if (block.type === "heading") {
      const el = document.createElement(`h${block.level}`);
      el.className = `md-h md-h${block.level}`;
      appendInline(el, block.text);
      return el;
    }
    if (block.type === "list") {
      const el = document.createElement(block.ordered ? "ol" : "ul");
      el.className = "md-list";
      for (const item of block.items) {
        const li = document.createElement("li");
        li.className = "md-li";
        appendInline(li, item);
        el.append(li);
      }
      return el;
    }
    if (block.type === "code") {
      const wrap = document.createElement("div");
      wrap.className = "md-codeblock";
      if (block.lang) {
        const label = document.createElement("span");
        label.className = "md-lang";
        label.textContent = block.lang;
        wrap.append(label);
      }
      const pre = document.createElement("pre");
      pre.className = "md-pre";
      const code = document.createElement("code");
      code.append(highlightCode(block.text, block.lang));
      pre.append(code);
      wrap.append(pre);
      return wrap;
    }
    if (block.type === "table") {
      const wrap = document.createElement("div");
      wrap.className = "md-table-wrap";
      const table = document.createElement("table");
      table.className = "md-table";
      const [header, ...body] = block.rows;
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const cell of header || []) {
        const th = document.createElement("th");
        appendInline(th, cell);
        headRow.append(th);
      }
      thead.append(headRow);
      table.append(thead);
      if (body.length) {
        const tbody = document.createElement("tbody");
        for (const row of body) {
          const tr = document.createElement("tr");
          for (const cell of row) {
            const td = document.createElement("td");
            appendInline(td, cell);
            tr.append(td);
          }
          tbody.append(tr);
        }
        table.append(tbody);
      }
      wrap.append(table);
      return wrap;
    }
    const p = document.createElement("p");
    p.className = "md-p";
    appendInline(p, block.text);
    return p;
  }

  function renderMarkdown(target, source) {
    if (!target) {
      return;
    }
    target.replaceChildren();
    const blocks = parseBlocks(source);
    if (!blocks.length) {
      return;
    }
    const frag = document.createDocumentFragment();
    for (const block of blocks) {
      frag.append(renderBlock(block));
    }
    target.append(frag);
  }

  root.renderMarkdown = renderMarkdown;
})(typeof window !== "undefined" ? window : globalThis);
