export interface Command<T> {
  readonly label: string;
  execute(state: T): T;
  undo(state: T): T;
}

export class CommandHistory<T> {
  private undoStack: Command<T>[] = [];
  private redoStack: Command<T>[] = [];

  execute(command: Command<T>, state: T): T {
    const next = command.execute(state);
    this.undoStack.push(command);
    this.redoStack = [];
    return next;
  }

  undo(state: T): T {
    const command = this.undoStack.pop();
    if (!command) return state;
    const next = command.undo(state);
    this.redoStack.push(command);
    return next;
  }

  redo(state: T): T {
    const command = this.redoStack.pop();
    if (!command) return state;
    const next = command.execute(state);
    this.undoStack.push(command);
    return next;
  }

  clear(): void {
    this.undoStack = [];
    this.redoStack = [];
  }

  get canUndo(): boolean { return this.undoStack.length > 0; }
  get canRedo(): boolean { return this.redoStack.length > 0; }
  get size(): number { return this.undoStack.length; }
}
