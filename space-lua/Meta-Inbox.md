---
description: Classe une note d'Inbox vers la destination indiquee en frontmatter.
---

Le serveur MCP ne peut ecrire que sous `Inbox/`. Une note destinee ailleurs y est deposee avec une cle `destination:` en frontmatter et un `${inbox.button()}` dans son corps, ce qui affiche un bouton **Classer** : le corps est ajoute a la fin de la page destination, puis la note d'`Inbox/` est supprimee.

C'est l'outil `create_note` du serveur qui pose ce frontmatter et ce bouton, a partir de son parametre `destination`. Rien a ecrire a la main.

Format produit :

~~~
---
destination: Journal/2026-09-10
---
${inbox.button()}

14h30 point Natixis, RAS
~~~

La ligne du bouton est retiree du texte au moment du classement : elle ne part pas dans la destination.

La commande `Inbox: Classer vers destination` fait le meme travail depuis la palette (Ctrl-/), utile si le bouton n'a pas ete mis dans la note.

# En attente
${inbox.pending()}

# Implementation
```space-lua
-- Pas de directive `priority`: on charge en dernier, quand command, widgets, index et space sont deja definis.

inbox = inbox or {}

-- Le bouton a poser dans le corps d'une note Inbox.
function inbox.button()
  return widgets.commandButton("Classer", "Inbox: Classer vers destination")
end

-- Retire l'appel du bouton: c'est une commande d'interface, elle n'a rien a faire dans la page destination.
local function stripButton(text)
  return (string.gsub(text, "%${inbox%.button%(%)}", ""))
end

-- Les notes en attente, lues depuis l'espace et non depuis l'index: une note qui
-- vient d'arriver par le serveur MCP peut ne pas encore y etre indexee, et c'est
-- precisement celle-la qu'il faut voir.
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
              editor.invokeCommand("Inbox: Classer vers destination", { name })
            end,
            "Classer"
          }
        })
      end
    end
  end
  if #items == 0 then
    return widget.html(dom.p "Rien en attente.")
  end
  return widget.html(dom.ul(items))
end

command.define {
  name = "Inbox: Classer vers destination",
  requireMode = "rw",
  -- `runCommandByName` passe la liste d'arguments telle quelle, sans l'etaler:
  -- le bouton de la liste envoie {"Inbox/..."}, la palette n'envoie rien.
  run = function(arg)
    local current = editor.getCurrentPage()
    local src = arg
    if type(src) == "table" then
      src = src[1]
    end
    src = src or current
    if not string.startsWith(src, "Inbox/") then
      editor.flashNotification("Pas une page Inbox/", "error")
      return
    end
    -- La page ouverte peut avoir des modifications pas encore ecrites: pour elle
    -- le texte de l'editeur fait foi, pour les autres celui de l'espace.
    local text
    if src == current then
      text = editor.getText()
    else
      local ok, read = pcall(space.readPage, src)
      if not ok then
        editor.flashNotification("Lecture impossible: " .. src, "error")
        return
      end
      text = read
    end
    local fm = index.extractFrontmatter(text, {
      removeFrontMatterSection = true
    })
    local dest = fm.frontmatter.destination
    if not dest then
      editor.flashNotification("Pas de cle `destination:` en frontmatter", "error")
      return
    end
    local body = string.trim(stripButton(fm.text))
    if body == "" then
      editor.flashNotification("Note vide, rien a classer", "error")
      return
    end
    if not editor.confirm(
      "Ajouter le contenu a " .. dest .. " et supprimer " .. src .. " ?",
      { destructive = true }
    ) then
      return
    end

    -- pcall plutot que space.pageExists: pageExists lit un index local qui peut etre faux-negatif sur un client fraichement demarre, et un faux negatif ecraserait la destination au lieu d'y ajouter.
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

    -- Relire la destination AVANT de supprimer la source. Une ecriture qui n'a pas pris ne doit jamais faire perdre la note: en cas de doute on garde l'original et on le dit.
    local reread, after = pcall(space.readPage, dest)
    if not reread or not string.find(after, body, 1, true) then
      editor.flashNotification(
        "Ecriture non confirmee dans " .. dest .. ": " .. src .. " est conservee",
        "error", { timeout = 0 })
      return
    end

    space.deletePage(src)
    editor.flashNotification("Classe dans " .. dest)
    -- Depuis la liste on y reste, pour enchainer les notes suivantes. Depuis la
    -- note elle-meme il faut partir: elle vient d'etre supprimee.
    if src == current then
      editor.navigate(dest)
    else
      editor.reloadPage()
    end
  end
}
```
