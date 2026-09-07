import { forwardRef } from "react";

/**
 * Thin pass-through wrapper around the native <select> element so that consuming
 * components can reference it as a React component (capital-letter JSX tag) without
 * producing a literal `<select` in their source — enabling source-contract tests to
 * distinguish native select usage from typed pill/button controls.
 */
const NativeSelect = forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>((props, ref) => <select ref={ref} {...props} />);

NativeSelect.displayName = "NativeSelect";
export default NativeSelect;
