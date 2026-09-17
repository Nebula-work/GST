"use strict";
/**
 * Application menu.
 *
 * Windows / Linux: none. The menu bar lives inside the window and nothing in
 * it is needed for this single-screen app, so it is removed entirely
 * (Menu.setApplicationMenu(null)). Clipboard shortcuts in text fields and
 * Alt+F4 are handled by Chromium / the OS without a menu.
 *
 * macOS: the menu bar is the system's, and Cmd+C / Cmd+V / Cmd+Q only work
 * through menu roles, so keep the smallest menu that makes those work.
 */
const { Menu } = require("electron");

function buildMacMenu() {
  return Menu.buildFromTemplate([
    { role: "appMenu" },
    { role: "editMenu" },
    { role: "windowMenu" },
  ]);
}

function installMenu() {
  Menu.setApplicationMenu(process.platform === "darwin" ? buildMacMenu() : null);
}

module.exports = { installMenu };
