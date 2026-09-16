---
name: GitHub connector sync
description: How to read and publish repository changes when local git HTTPS authentication is unavailable.
---

The GitHub Replit integration can authenticate GitHub REST API calls without configuring credentials for the workspace's local Git commands. Remote commit history can therefore be newer than the local `origin` ref.

**Why:** A normal HTTPS push can still fail with “Invalid username or token” after the GitHub connection is attached.

**How to apply:** Use the connected GitHub API to inspect commits and files before merging remote work. For publishing, create blobs, build a tree from the current remote branch, create one commit, and update the branch ref with `force: false`. Never request or expose a personal access token in chat.