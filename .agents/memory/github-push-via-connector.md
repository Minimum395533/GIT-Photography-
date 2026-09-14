---
name: GitHub connector pushes
description: How to publish repository changes when the GitHub integration is connected but local git HTTPS authentication is unavailable.
---

The GitHub Replit integration can authenticate GitHub REST API calls without configuring credentials for the workspace's local `git push` command.

**Why:** A normal HTTPS push can still fail with “Invalid username or token” after the GitHub connection is attached.

**How to apply:** Use the connected GitHub API to create blobs, build a tree from the current remote branch, create one commit, and update the branch ref with `force: false`. Never request or expose a personal access token in chat.