// The core build never loads this controller; keep accidental calls explicit.
export class ShadePanelController {
  constructor() {
    throw new Error("ShadeMap is not included in this build");
  }
}
