---
description: Classe une note d'Inbox vers la destination indiquee en frontmatter.
---

Les notes ecrites par le serveur MCP arrivent dans `Inbox/`, seul prefixe ou il a le droit d'ecrire. Une note qui porte une cle `destination:` en frontmatter et un `${inbox.button()}` dans son corps affiche un bouton **Classer** : le corps est ajoute a la fin de la page destination, puis la note d'`Inbox/` est supprimee.

Format attendu :

~~~
---
destination: Journal/2026-09-10
---
${inbox.button()}

14h30 point Natixis, RAS
~~~

La ligne du bouton est retiree du texte au moment du classement : elle ne part pas dans la destination.

La commande `Inbox: Classer vers destination` fait le meme travail depuis la palette (Ctrl-/), utile si le bouton n'a pas ete mis dans la note.

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

command.define {
  name = "Inbox: Classer vers destination",
  requireMode = "rw",
  run = function()
    local src = editor.getCurrentPage()
    if not string.startsWith(src, "Inbox/") then
      editor.flashNotification("Pas une page Inbox/", "error")
      return
    end
    local fm = index.extractFrontmatter(editor.getText(), {
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

    -- Supprimer puis quitter la page, comme le fait `Page: Delete`.
    space.deletePage(src)
    editor.navigate(dest)
    editor.flashNotification("Classe dans " .. dest)
  end
}
```
