import { useState, useCallback, useEffect } from "react";
import { Folder, FolderOpen, GitBranch, ArrowUp, Link, HardDrive } from "lucide-react";
import { toast } from "sonner";
import { registerRepo, browseDirectories } from "../api/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody } from "./ui/dialog";
import { Spinner } from "./ui/spinner";

interface AddRepoModalProps {
  opened: boolean;
  onClose: () => void;
  onAdded: (path: string) => void;
}

export function AddRepoModal({ opened, onClose, onAdded }: AddRepoModalProps) {
  const [tab, setTab] = useState("path");
  const [input, setInput] = useState("");
  const [cloneTo, setCloneTo] = useState("");
  const [adding, setAdding] = useState(false);

  const [browsePath, setBrowsePath] = useState("~");
  const [browseEntries, setBrowseEntries] = useState<{ name: string; path: string; isGitRepo: string }[]>([]);
  const [browseParent, setBrowseParent] = useState<string | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);

  const handleAdd = useCallback(
    async (source: string, cloneTarget?: string) => {
      if (!source.trim()) return;
      setAdding(true);
      try {
        const result = await registerRepo(source.trim(), cloneTarget?.trim() || undefined);
        toast.success(`Added: ${result.path.split("/").pop()}`);
        onAdded(result.path);
        setInput("");
        setCloneTo("");
        onClose();
      } catch (e) {
        toast.error(String(e));
      } finally {
        setAdding(false);
      }
    },
    [onAdded, onClose],
  );

  const loadDirectory = useCallback(async (path: string) => {
    setBrowseLoading(true);
    try {
      const result = await browseDirectories(path);
      setBrowsePath(result.current);
      setBrowseParent(result.parent);
      setBrowseEntries(result.items);
    } catch {
      toast.error("Failed to browse directory");
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "browse" && browseEntries.length === 0) {
      loadDirectory("~");
    }
  }, [tab, browseEntries.length, loadDirectory]);

  return (
    <Dialog open={opened} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Repository</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="mb-4">
              <TabsTrigger value="path">
                <HardDrive size={13} />
                Local Path
              </TabsTrigger>
              <TabsTrigger value="url">
                <Link size={13} />
                Git URL
              </TabsTrigger>
              <TabsTrigger value="browse">
                <Folder size={13} />
                Browse
              </TabsTrigger>
            </TabsList>

            <TabsContent value="path">
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label>Local path</Label>
                  <Input
                    placeholder="/home/user/projects/my-repo"
                    value={input}
                    onChange={(e) => setInput(e.currentTarget.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleAdd(input)}
                  />
                </div>
                <div className="flex justify-end">
                  <Button loading={adding} disabled={!input.trim()} onClick={() => handleAdd(input)}>
                    Add Repository
                  </Button>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="url">
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label>Git URL</Label>
                  <Input
                    placeholder="https://github.com/user/repo.git"
                    value={input}
                    onChange={(e) => setInput(e.currentTarget.value)}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Clone to</Label>
                  <Input
                    placeholder="/home/user/projects/repo"
                    value={cloneTo}
                    onChange={(e) => setCloneTo(e.currentTarget.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Local directory where the repository will be cloned
                  </p>
                </div>
                <div className="flex justify-end">
                  <Button loading={adding} disabled={!input.trim() || !cloneTo.trim()} onClick={() => handleAdd(input, cloneTo)}>
                    Clone &amp; Add
                  </Button>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="browse">
              <div className="rounded-md border border-border bg-background px-3 py-2 mb-2 flex items-center gap-2">
                {browseParent && (
                  <button
                    type="button"
                    onClick={() => loadDirectory(browseParent)}
                    className="p-0.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                  >
                    <ArrowUp size={14} />
                  </button>
                )}
                <span className="text-xs font-mono text-muted-foreground truncate flex-1">{browsePath}</span>
              </div>

              <div className="h-[min(250px,50vh)] overflow-y-auto rounded-md border border-border">
                {browseLoading ? (
                  <div className="flex justify-center py-8">
                    <Spinner size="sm" />
                  </div>
                ) : browseEntries.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-6">No subdirectories</p>
                ) : (
                  <div className="p-1 flex flex-col gap-px">
                    {browseEntries.map((entry) => {
                      const isGit = entry.isGitRepo === "true";
                      return (
                        <button
                          key={entry.path}
                          type="button"
                          className="flex w-full items-center justify-between gap-2 px-2 py-1.5 rounded hover:bg-accent cursor-pointer text-left"
                          onClick={() =>
                            isGit ? handleAdd(entry.path) : loadDirectory(entry.path)
                          }
                        >
                          <div className="flex items-center gap-2">
                            {isGit ? (
                              <GitBranch size={14} className="text-green-500 shrink-0" />
                            ) : (
                              <FolderOpen size={14} className="text-yellow-500 shrink-0" />
                            )}
                            <span className="text-sm">{entry.name}</span>
                          </div>
                          {isGit && (
                            <span className="text-xs text-green-400">git repo — click to add</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </TabsContent>
          </Tabs>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
