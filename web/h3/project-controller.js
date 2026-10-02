/**
 * H3 Studio Lab — Project Controller
 *
 * Manages project state, revision tracking, atomic saves, and Qwen image handoff.
 * All client-side storage keys are namespaced by server identity to avoid cross-server collision.
 */

(function(root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.H3ProjectController = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  class ProjectController {
    constructor(options = {}) {
      this.apiBase = options.apiBase || '';
      this.serverKey = options.serverKey || 'default';
      this.currentProject = null;
      this.onProjectChanged = options.onProjectChanged || (() => {});
    }

    _storageKey(key) {
      return `h3_lab.${this.serverKey}.${key}`;
    }

    apiUrl(endpoint) {
      return (this.apiBase || '').replace(/\/$/, '') + endpoint;
    }

    async fetchJson(endpoint, options = {}) {
      const res = await fetch(this.apiUrl(endpoint), {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          msg = errData.error || errData.message || msg;
        } catch {}
        const err = new Error(msg);
        err.status = res.status;
        throw err;
      }
      return await res.json();
    }

    async createProject(name = 'New Project', canvas = null) {
      const data = await this.fetchJson('/h3_studio/lab/projects', {
        method: 'POST',
        body: JSON.stringify({ name, canvas }),
      });
      this.currentProject = data;
      localStorage.setItem(this._storageKey('active_project_id'), data.project_id);
      this.onProjectChanged(this.currentProject);
      return data;
    }

    async loadProject(projectId) {
      const data = await this.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(projectId)}`);
      this.currentProject = data;
      localStorage.setItem(this._storageKey('active_project_id'), data.project_id);
      this.onProjectChanged(this.currentProject);
      return data;
    }

    async saveCurrentProject(updates = {}) {
      if (!this.currentProject) {
        throw new Error('No active project loaded');
      }
      const expectedRev = this.currentProject.revision;
      const merged = { ...this.currentProject, ...updates };

      const saved = await this.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(this.currentProject.project_id)}`, {
        method: 'POST',
        body: JSON.stringify({
          project: merged,
          expected_revision: expectedRev,
        }),
      });

      this.currentProject = saved;
      this.onProjectChanged(this.currentProject);
      return saved;
    }

    async listProjects() {
      return await this.fetchJson('/h3_studio/lab/projects');
    }

    async duplicateCurrentProject(newName = null) {
      if (!this.currentProject) throw new Error('No active project loaded');
      const copy = await this.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(this.currentProject.project_id)}/duplicate`, {
        method: 'POST',
        body: JSON.stringify({ name: newName }),
      });
      this.currentProject = copy;
      localStorage.setItem(this._storageKey('active_project_id'), copy.project_id);
      this.onProjectChanged(this.currentProject);
      return copy;
    }

    async exportBundle(projectId) {
      const pid = projectId || this.currentProject?.project_id;
      if (!pid) throw new Error('No project to export');
      return this.apiUrl(`/h3_studio/lab/projects/${encodeURIComponent(pid)}/export`);
    }

    async importQwenImageToH3(imageFilename, asRole = 'reference', alias = null) {
      if (!this.currentProject) {
        await this.createProject('Qwen Project');
      }
      const data = await this.fetchJson('/h3_studio/lab/import_qwen_image', {
        method: 'POST',
        body: JSON.stringify({
          image_filename: imageFilename,
          project_id: this.currentProject.project_id,
          as_role: asRole,
          alias: alias,
        }),
      });
      await this.loadProject(this.currentProject.project_id);
      return data;
    }
  }

  return {
    ProjectController,
  };
});
