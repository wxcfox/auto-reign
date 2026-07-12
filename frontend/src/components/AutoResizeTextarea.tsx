"use client";

import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  type ChangeEvent,
  type TextareaHTMLAttributes,
} from "react";

type AutoResizeTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  maxHeight?: number;
};

export const AutoResizeTextarea = forwardRef<HTMLTextAreaElement, AutoResizeTextareaProps>(
  function AutoResizeTextarea(
    { maxHeight = 180, onChange, value, ...props },
    forwardedRef,
  ) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

    useImperativeHandle(forwardedRef, () => textareaRef.current as HTMLTextAreaElement, []);

    const resize = useCallback(
      (textarea: HTMLTextAreaElement | null) => {
        if (!textarea) {
          return;
        }
        textarea.style.height = "auto";
        const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
        textarea.style.height = `${nextHeight}px`;
        textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
      },
      [maxHeight],
    );

    useLayoutEffect(() => {
      resize(textareaRef.current);
    }, [resize, value]);

    useLayoutEffect(() => {
      const handleResize = () => resize(textareaRef.current);
      window.addEventListener("resize", handleResize);
      return () => window.removeEventListener("resize", handleResize);
    }, [resize]);

    function handleChange(event: ChangeEvent<HTMLTextAreaElement>) {
      resize(event.currentTarget);
      onChange?.(event);
    }

    return (
      <textarea
        {...props}
        onChange={handleChange}
        ref={textareaRef}
        rows={props.rows ?? 1}
        value={value}
      />
    );
  },
);
