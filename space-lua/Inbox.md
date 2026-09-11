---
name: Library/jgurhem/Inbox
tags: meta/library
description: Files an Inbox note to the page given by its frontmatter.
---

The MCP server [mcp-silverbullet](https://github.com/jgurhem/MCP-Silverbullet) can only write under a single prefix, `Inbox/`. A note meant for somewhere else is dropped there with a `destination:` frontmatter key and a `${inbox.button()}` in its body, which displays a **File** button: the body is appended to the end of the destination page, then the note in `Inbox/` is deleted.

It is the server's `create_note` tool that adds this frontmatter and this button, from its `destination` parameter. Nothing to write by hand.

Format produced:

~~~
---
destination: Journal/2026-09-10
---
${inbox.button()}

2:30pm client sync, nothing to report
~~~

The button line is removed from the text at filing time: it does not go to the destination.

The `Inbox: File to destination` command does the same job from the palette (Ctrl-/), useful if the button was not put in the note.

# Dashboard
This page is overwritten on every library update, so the list of pending notes
lives elsewhere. Create a page — `Meta/Inbox` for instance — whose body is a
single call:

~~~
${inbox.pending()}
~~~

Each row carries the note's name, its destination and its own **File** button;
notes are filed one after another without leaving the list.

# Implementation
```space-lua
-- No `priority` directive: we load last, when command, widgets, index and space are already defined.

inbox = inbox or {}

-- The button to place in the body of an Inbox note.
function inbox.button()
  return widgets.commandButton("File", "Inbox: File to destination")
end

-- Removes the button call: it is a UI command, it has no business in the destination page.
local function stripButton(text)
  return (string.gsub(text, "%${inbox%.button%(%)}", ""))
end

-- Pending notes, read from the space and not from the index: a note that just
-- arrived through the MCP server may not be indexed yet, and that is precisely
-- the one that needs to be seen.
function inbox.pending()
  local items = {}
  for _, page in ipairs(space.listPages()) do
    if string.startsWith(page.name, "Inbox/") then
      local ok, text = pcall(space.readPage, page.name)
      local dest = ok and index.extractFrontmatter(text).frontmatter.destination
      if dest then
        local name = page.name
        table.insert(items, dom.li {
          dom.a {
            onclick = function() editor.navigate(name) end,
            style = "cursor: pointer",
            name
          },
          " → " .. dest .. " ",
          dom.button {
            onclick = function()
              editor.invokeCommand("Inbox: File to destination", { name })
            end,
            "File"
          }
        })
      end
    end
  end
  if #items == 0 then
    return widget.html(dom.p "Nothing pending.")
  end
  return widget.html(dom.ul(items))
end

command.define {
  name = "Inbox: File to destination",
  requireMode = "rw",
  -- `runCommandByName` passes the argument list as is, without spreading it:
  -- the list button sends {"Inbox/..."}, the palette sends nothing.
  run = function(arg)
    local current = editor.getCurrentPage()
    local src = arg
    if type(src) == "table" then
      src = src[1]
    end
    src = src or current
    if not string.startsWith(src, "Inbox/") then
      editor.flashNotification("Not an Inbox/ page", "error")
      return
    end
    -- The open page may have changes not written yet: for it the editor text is
    -- authoritative, for the others the space text is.
    local text
    if src == current then
      text = editor.getText()
    else
      local ok, read = pcall(space.readPage, src)
      if not ok then
        editor.flashNotification("Cannot read: " .. src, "error")
        return
      end
      text = read
    end
    local fm = index.extractFrontmatter(text, {
      removeFrontMatterSection = true
    })
    local dest = fm.frontmatter.destination
    if not dest then
      editor.flashNotification("No `destination:` key in frontmatter", "error")
      return
    end
    local body = string.trim(stripButton(fm.text))
    if body == "" then
      editor.flashNotification("Empty note, nothing to file", "error")
      return
    end
    if not editor.confirm(
      "Append the content to " .. dest .. " and delete " .. src .. "?",
      { destructive = true }
    ) then
      return
    end

    -- pcall rather than space.pageExists: pageExists reads a local index that can be a false negative on a freshly started client, and a false negative would overwrite the destination instead of appending to it.
    local ok, existing = pcall(space.readPage, dest)
    if not ok then
      existing = ""
    end
    local merged
    if string.trim(existing) == "" then
      merged = body .. "\n"
    else
      merged = string.trimEnd(existing) .. "\n\n" .. body .. "\n"
    end
    space.writePage(dest, merged)

    -- Re-read the destination BEFORE deleting the source. A write that did not take must never cost the note: when in doubt we keep the original and say so.
    local reread, after = pcall(space.readPage, dest)
    if not reread or not string.find(after, body, 1, true) then
      editor.flashNotification(
        "Write not confirmed in " .. dest .. ": " .. src .. " is kept",
        "error", { timeout = 0 })
      return
    end

    space.deletePage(src)
    editor.flashNotification("Filed in " .. dest)
    -- From the list we stay there, to file the next notes. From the note itself
    -- we must leave: it has just been deleted.
    if src == current then
      editor.navigate(dest)
    else
      editor.reloadPage()
    end
  end
}
```
