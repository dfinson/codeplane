import { useCallback, useEffect, useState } from "react";
import { FolderGit2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  createProject,
  createRepo,
  registerRepo,
  unregisterRepo,
} from "../api/client";
import type { ProjectResponse } from "../api/types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { DirectoryField } from "./DirectoryField";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
} from "./ui/dialog";

type AddRepoMode = "existing" | "clone" | "local";

interface CreateProjectDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (project: ProjectResponse) => void;
}

function joinPath(parent: string, child: string): string {
  const separator = parent.includes("\\") ? "\\" : "/";
  if (parent.endsWith("/") || parent.endsWith("\\")) return `${parent}${child}`;
  return `${parent}${separator}${child}`;
}

function suggestCloneFolderName(source: string): string {
  const trimmed = source.trim().replace(/[\\/]+$/, "");
  const lastSegment = trimmed.split(/[\\/]/).pop() ?? "repo";
  return lastSegment.endsWith(".git") ? lastSegment.slice(0, -4) : lastSegment;
}

export function CreateProjectDialog({ open, onClose, onCreated }: CreateProjectDialogProps) {
  const [name, setName] = useState("");
  const [repoPaths, setRepoPaths] = useState<string[]>([]);
  const [stagedRepoPaths, setStagedRepoPaths] = useState<string[]>([]);
  const [mode, setMode] = useState<AddRepoMode>("existing");
  const [existingRepoPath, setExistingRepoPath] = useState("");
  const [cloneSource, setCloneSource] = useState("");
  const [cloneDestination, setCloneDestination] = useState("");
  const [cloneFolderName, setCloneFolderName] = useState("");
  const [initPath, setInitPath] = useState("");
  const [initName, setInitName] = useState("");
  const [addingRepo, setAddingRepo] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetState = useCallback(() => {
    setName("");
    setRepoPaths([]);
    setStagedRepoPaths([]);
    setMode("existing");
    setExistingRepoPath("");
    setCloneSource("");
    setCloneDestination("");
    setCloneFolderName("");
    setInitPath("");
    setInitName("");
    setError(null);
  }, []);

  useEffect(() => {
    if (!open) return;
    resetState();
  }, [open, resetState]);

  function addRepoPath(path: string) {
    const trimmed = path.trim();
    if (!trimmed) return;
    setRepoPaths((items) => (items.includes(trimmed) ? items : [...items, trimmed]));
  }

  async function compensateRegistrations(paths: string[]) {
    const results = await Promise.allSettled(paths.map((path) => unregisterRepo(path)));
    return paths.filter((_, index) => results[index]?.status === "rejected");
  }

  function removeRepoPath(path: string) {
    setRepoPaths((items) => items.filter((item) => item !== path));
    if (stagedRepoPaths.includes(path)) {
      setStagedRepoPaths((items) => items.filter((item) => item !== path));
      void compensateRegistrations([path]).then((failed) => {
        if (failed.length > 0) toast.error(`Could not undo repository registration for ${path}.`);
      });
    }
  }

  const handleAddExisting = useCallback(() => {
    if (!existingRepoPath.trim()) {
      setError("Choose an existing repository path to add.");
      return;
    }
    setError(null);
    addRepoPath(existingRepoPath);
  }, [existingRepoPath]);

  const handleAddViaClone = useCallback(async () => {
    if (!cloneSource.trim()) {
      setError("Enter a repository URL to clone.");
      return;
    }
    if (!cloneDestination.trim()) {
      setError("Choose a local destination for the cloned repository.");
      return;
    }
    const folderName = (cloneFolderName.trim() || suggestCloneFolderName(cloneSource)).trim();
    if (!folderName) {
      setError("Enter a folder name for the cloned repository.");
      return;
    }
    if (folderName === "." || folderName === ".." || /[\\/]/.test(folderName)) {
      setError("Folder name must be a single directory.");
      return;
    }

    setAddingRepo(true);
    setError(null);
    try {
      const targetPath = joinPath(cloneDestination.trim(), folderName);
      const res = await registerRepo(cloneSource.trim(), targetPath, "clone");
      addRepoPath(res.path);
      if (res.registered) {
        setStagedRepoPaths((items) => (items.includes(res.path) ? items : [...items, res.path]));
      }
      setCloneSource("");
      setCloneDestination("");
      setCloneFolderName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clone repository");
    } finally {
      setAddingRepo(false);
    }
  }, [cloneDestination, cloneFolderName, cloneSource]);

  const handleAddViaInit = useCallback(async () => {
    if (!initPath.trim()) {
      setError("Choose a parent directory for the new repository.");
      return;
    }
    setAddingRepo(true);
    setError(null);
    try {
      const res = await createRepo(initPath.trim(), initName.trim() || undefined);
      addRepoPath(res.path);
      setStagedRepoPaths((items) => (items.includes(res.path) ? items : [...items, res.path]));
      setInitPath("");
      setInitName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create repository");
    } finally {
      setAddingRepo(false);
    }
  }, [initName, initPath]);

  const handleCreate = useCallback(async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Project name is required.");
      return;
    }
    if (repoPaths.length === 0) {
      setError("Add at least one member repository before creating the Project.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const project = await createProject({ name: trimmedName, repoPaths });
      const cleanupFailures = await compensateRegistrations(stagedRepoPaths);
      if (cleanupFailures.length > 0) {
        toast.error(
          `Project saved, but legacy registration remains for: ${cleanupFailures.join(", ")}`,
        );
      }
      setStagedRepoPaths([]);
      onCreated(project);
      onClose();
    } catch (err) {
      const failed = await compensateRegistrations(stagedRepoPaths);
      setStagedRepoPaths([]);
      const reason = err instanceof Error ? err.message : "Failed to create Project";
      const retained = stagedRepoPaths.length > 0
        ? ` Repository files remain at: ${stagedRepoPaths.join(", ")}. Remove them manually if unwanted.`
        : "";
      const rollback = failed.length > 0
        ? ` Registration rollback also failed for: ${failed.join(", ")}.`
        : "";
      setError(`${reason}.${retained}${rollback}`);
    } finally {
      setCreating(false);
    }
  }, [name, repoPaths, stagedRepoPaths, onCreated, onClose]);

  const handleClose = useCallback(async () => {
    if (stagedRepoPaths.length > 0) {
      const failed = await compensateRegistrations(stagedRepoPaths);
      if (failed.length > 0) {
        toast.error(`Could not undo repository registration for: ${failed.join(", ")}`);
      }
      toast.info(`Repository files remain at: ${stagedRepoPaths.join(", ")}`);
    }
    onClose();
  }, [onClose, stagedRepoPaths]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !creating && !addingRepo && void handleClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderGit2 size={16} className="text-muted-foreground" />
            New Project
          </DialogTitle>
          <DialogDescription>
            Group one or more repositories under a single Project so their board, chats, and
            settings can be managed together.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          <div className="space-y-1.5">
            <label htmlFor="create-project-name" className="text-xs text-muted-foreground">
              Project name
            </label>
            <Input
              id="create-project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Payments Platform"
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <label className="text-xs text-muted-foreground">Member repositories</label>
              <span className="text-[10px] text-muted-foreground">{repoPaths.length} added</span>
            </div>
            {repoPaths.length === 0 ? (
              <p className="text-xs text-muted-foreground">No repositories added yet.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {repoPaths.map((path) => (
                  <div
                    key={path}
                    className="flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-[10px] font-mono"
                  >
                    <span className="truncate max-w-[16rem]">{path}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${path}`}
                      className="text-muted-foreground hover:text-foreground"
                      onClick={() => removeRepoPath(path)}
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-3 rounded-lg border border-border bg-background p-3">
            <div className="flex gap-1 rounded-md bg-muted p-0.5 w-fit">
              <button
                type="button"
                className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${mode === "existing" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => setMode("existing")}
              >
                Add existing repository
              </button>
              <button
                type="button"
                className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${mode === "clone" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => setMode("clone")}
              >
                Clone repository
              </button>
              <button
                type="button"
                className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${mode === "local" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => setMode("local")}
              >
                Create local repository
              </button>
            </div>

            {mode === "existing" && (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Browse to an existing local git repository, then add it to this Project.
                </p>
                <DirectoryField label="Repository path" value={existingRepoPath} onChange={setExistingRepoPath} />
                <Button type="button" variant="secondary" onClick={handleAddExisting}>
                  <Plus size={12} />
                  Add repository
                </Button>
              </div>
            )}

            {mode === "clone" && (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Clone a remote repository into a local destination, then add it to this Project.
                </p>
                <Input
                  value={cloneSource}
                  onChange={(event) => setCloneSource(event.target.value)}
                  placeholder="Repository URL"
                  aria-label="Repository URL"
                />
                <DirectoryField label="Local destination" value={cloneDestination} onChange={setCloneDestination} />
                <Input
                  value={cloneFolderName}
                  onChange={(event) => setCloneFolderName(event.target.value)}
                  placeholder="Folder name override (optional)"
                  aria-label="Folder name override"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleAddViaClone}
                  loading={addingRepo}
                >
                  <Plus size={12} />
                  Clone repository
                </Button>
              </div>
            )}

            {mode === "local" && (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Initialize a brand-new git repository in a local directory, then add it to this Project.
                </p>
                <DirectoryField label="Parent directory" value={initPath} onChange={setInitPath} />
                <Input
                  value={initName}
                  onChange={(event) => setInitName(event.target.value)}
                  placeholder="Repository name (optional)"
                  aria-label="Repository name"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleAddViaInit}
                  loading={addingRepo}
                >
                  <Plus size={12} />
                  Create repository
                </Button>
              </div>
            )}
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}
        </DialogBody>

        <DialogFooter>
          <Button variant="ghost" onClick={() => void handleClose()} disabled={creating || addingRepo}>
            Cancel
          </Button>
          <Button onClick={handleCreate} loading={creating}>
            Create Project
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
