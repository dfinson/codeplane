import { useCallback, useLayoutEffect, useRef, useState } from "react";
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
  const formGeneration = useRef(0);
  const repoOperation = useRef({ generation: 0, target: "", active: false });
  const repoTarget = JSON.stringify([mode, cloneSource, cloneDestination, cloneFolderName, initPath, initName]);
  const formTarget = JSON.stringify([name, repoPaths]);

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

  useLayoutEffect(() => {
    formGeneration.current += 1;
    setCreating(false);
    if (open) resetState();
    return () => { formGeneration.current += 1; };
  }, [open, resetState]);

  useLayoutEffect(() => {
    formGeneration.current += 1;
    setCreating(false);
  }, [formTarget]);

  useLayoutEffect(() => {
    repoOperation.current = { generation: repoOperation.current.generation + 1, target: repoTarget, active: false };
    setAddingRepo(false);
    return () => { repoOperation.current.generation += 1; };
  }, [open, repoTarget]);

  const beginRepoOperation = useCallback(() => {
    if (repoOperation.current.active) return null;
    const operation = { generation: repoOperation.current.generation + 1, target: repoTarget, active: true };
    repoOperation.current = operation;
    return { ...operation };
  }, [repoTarget]);

  const currentRepoOperation = useCallback((operation: { generation: number; target: string }) => {
    return repoOperation.current.generation === operation.generation && repoOperation.current.target === operation.target;
  }, []);

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

    const operation = beginRepoOperation();
    if (!operation) return;
    setAddingRepo(true);
    setError(null);
    try {
      const targetPath = joinPath(cloneDestination.trim(), folderName);
      const res = await registerRepo(cloneSource.trim(), targetPath, "clone");
      if (!currentRepoOperation(operation)) {
        if (res.registered) {
          const failed = await compensateRegistrations([res.path]);
          if (failed.length) toast.error(`Could not undo repository registration for ${res.path}.`);
        }
        toast.info(`Clone completed at ${res.path}. Files remain, but the changed Project form was not updated.`);
        return;
      }
      addRepoPath(res.path);
      if (res.registered) {
        setStagedRepoPaths((items) => (items.includes(res.path) ? items : [...items, res.path]));
      }
      setCloneSource("");
      setCloneDestination("");
      setCloneFolderName("");
    } catch (err) {
      const reason = err instanceof Error ? err.message : "Failed to clone repository";
      if (currentRepoOperation(operation)) setError(reason);
      else toast.error(`Earlier clone request: ${reason}`);
    } finally {
      if (currentRepoOperation(operation)) {
        repoOperation.current.active = false;
        setAddingRepo(false);
      }
    }
  }, [cloneDestination, cloneFolderName, cloneSource, beginRepoOperation, currentRepoOperation]);

  const handleAddViaInit = useCallback(async () => {
    if (!initPath.trim()) {
      setError("Choose a parent directory for the new repository.");
      return;
    }
    const operation = beginRepoOperation();
    if (!operation) return;
    setAddingRepo(true);
    setError(null);
    try {
      const res = await createRepo(initPath.trim(), initName.trim() || undefined);
      if (!currentRepoOperation(operation)) {
        const failed = await compensateRegistrations([res.path]);
        if (failed.length) toast.error(`Could not undo repository registration for ${res.path}.`);
        toast.info(`Repository created at ${res.path}. Files remain, but the changed Project form was not updated.`);
        return;
      }
      addRepoPath(res.path);
      setStagedRepoPaths((items) => (items.includes(res.path) ? items : [...items, res.path]));
      setInitPath("");
      setInitName("");
    } catch (err) {
      const reason = err instanceof Error ? err.message : "Failed to create repository";
      if (currentRepoOperation(operation)) setError(reason);
      else toast.error(`Earlier repository creation request: ${reason}`);
    } finally {
      if (currentRepoOperation(operation)) {
        repoOperation.current.active = false;
        setAddingRepo(false);
      }
    }
  }, [initName, initPath, beginRepoOperation, currentRepoOperation]);

  const handleCreate = useCallback(async () => {
    if (creating) return;
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
    const generation = ++formGeneration.current;
    try {
      const project = await createProject({ name: trimmedName, repoPaths });
      const cleanupFailures = await compensateRegistrations(stagedRepoPaths);
      if (cleanupFailures.length > 0) {
        toast.error(
          `Project saved, but legacy registration remains for: ${cleanupFailures.join(", ")}`,
        );
      }
      if (formGeneration.current !== generation) {
        toast.info(`Project "${project.name}" was created. The current form was not changed.`);
        return;
      }
      setStagedRepoPaths([]);
      onCreated(project);
      onClose();
    } catch (err) {
      const failed = await compensateRegistrations(stagedRepoPaths);
      const reason = err instanceof Error ? err.message : "Failed to create Project";
      const retained = stagedRepoPaths.length > 0
        ? ` Repository files and any completed index remain at: ${stagedRepoPaths.join(", ")}. Remove them manually if unwanted.`
        : "";
      const rollback = failed.length > 0
        ? ` Registration rollback also failed for: ${failed.join(", ")}.`
        : "";
      if (formGeneration.current === generation) {
        setStagedRepoPaths([]);
        setError(`${reason}.${retained}${rollback}`);
      } else {
        toast.error(`Earlier Project creation request: ${reason}.${retained}${rollback}`);
      }
    } finally {
      if (formGeneration.current === generation) setCreating(false);
    }
  }, [name, repoPaths, stagedRepoPaths, onCreated, onClose, creating]);

  const handleClose = useCallback(async () => {
    const generation = ++formGeneration.current;
    repoOperation.current.generation += 1;
    setStagedRepoPaths([]);
    if (stagedRepoPaths.length > 0) {
      const failed = await compensateRegistrations(stagedRepoPaths);
      if (failed.length > 0) {
        toast.error(`Could not undo repository registration for: ${failed.join(", ")}`);
      }
      toast.info(`Repository files and any completed index remain at: ${stagedRepoPaths.join(", ")}`);
    }
    if (formGeneration.current === generation) onClose();
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
