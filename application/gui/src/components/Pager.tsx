import { faChevronLeft, faChevronRight } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";

/** Range label with Previous and Next buttons. */
export function Pager({
  label,
  canPrevious,
  canNext,
  onPrevious,
  onNext,
}: {
  label: string;
  canPrevious: boolean;
  canNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <div className="pager">
      <span className="text-xs text-faint">{label}</span>
      <div className="pager-actions">
        <button
          className="btn"
          type="button"
          title="Previous"
          aria-label="Previous"
          disabled={!canPrevious}
          onClick={onPrevious}
        >
          <FontAwesomeIcon icon={faChevronLeft} />
        </button>
        <button
          className="btn"
          type="button"
          title="Next"
          aria-label="Next"
          disabled={!canNext}
          onClick={onNext}
        >
          <FontAwesomeIcon icon={faChevronRight} />
        </button>
      </div>
    </div>
  );
}
