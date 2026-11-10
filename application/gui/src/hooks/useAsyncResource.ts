import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { api } from "../api";
import { errorMessage } from "../errors";

type Loader<T> = () => Promise<T>;

export type AsyncResourceOptions = {
  /** Set `data` to null when a load fails. Default: keep the last data. */
  clearOnError?: boolean;
};

export type AsyncResource<T> = {
  /** Last loaded value; null before the first success. */
  data: T | null;
  error: string | null;
  /** True while a request is in flight. */
  loading: boolean;
  /** True after the first load attempt has finished (success or failure). */
  loaded: boolean;
  /** Loads again. Never rejects; a failure is reported through `error`. */
  reload: () => Promise<void>;
  setData: Dispatch<SetStateAction<T | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
};

/**
 * Load a value when the component mounts and whenever `loader` changes.
 *
 * `loader` must have a stable identity (a module-level function or a
 * memoised callback). Pass `null` to load nothing. A response is discarded
 * when a newer request has started or the component has unmounted.
 */
export function useAsyncResource<T>(
  loader: Loader<T> | null,
  options: AsyncResourceOptions = {},
): AsyncResource<T> {
  const { clearOnError = false } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(loader !== null);
  const [loaded, setLoaded] = useState(false);
  const [seenLoader, setSeenLoader] = useState<Loader<T> | null>(() => loader);
  const seq = useRef(0);

  // Adjust `loading` during render when the loader changes, so the first
  // render with a new loader already reports loading.
  if (seenLoader !== loader) {
    setSeenLoader(() => loader);
    setLoading(loader !== null);
  }

  const reload = useCallback(async () => {
    if (loader === null) return;
    seq.current += 1;
    const mine = seq.current;
    setLoading(true);
    try {
      const next = await loader();
      if (mine !== seq.current) return;
      setData(next);
      setError(null);
    } catch (err) {
      if (mine !== seq.current) return;
      setError(errorMessage(err));
      if (clearOnError) setData(null);
    }
    setLoading(false);
    setLoaded(true);
  }, [loader, clearOnError]);

  useEffect(() => {
    void reload();
    return () => {
      // Invalidate the request that is in flight.
      seq.current += 1;
    };
  }, [reload]);

  return { data, error, loading, loaded, reload, setData, setError };
}

/** `useAsyncResource` for one GET path. Pass `null` to load nothing. */
export function useApiResource<T>(
  path: string | null,
  options: AsyncResourceOptions = {},
): AsyncResource<T> {
  const loader = useMemo<Loader<T> | null>(
    () => (path === null ? null : () => api<T>(path)),
    [path],
  );
  return useAsyncResource(loader, options);
}
