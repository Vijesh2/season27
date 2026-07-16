import Sortable from "sortablejs";

document.documentElement.classList.add("js");

const predictionForm = document.querySelector<HTMLFormElement>("#prediction-form");

if (predictionForm) {
  const list = predictionForm.querySelector<HTMLUListElement>(".prediction-list");
  const unsaved = document.querySelector<HTMLElement>(".unsaved");

  const refreshRows = (): void => {
    if (!list) return;
    const rows = Array.from(list.querySelectorAll<HTMLLIElement>(".prediction-row"));
    rows.forEach((row, index) => {
      const position = row.querySelector<HTMLElement>(".prediction-position");
      const up = row.querySelector<HTMLButtonElement>(".move-up");
      const down = row.querySelector<HTMLButtonElement>(".move-down");
      if (position) position.textContent = String(index + 1);
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows.length - 1;
    });
  };

  predictionForm.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".move-button");
    if (!button || !list) return;
    event.preventDefault();
    const row = button.closest<HTMLLIElement>(".prediction-row");
    if (!row) return;
    if (button.classList.contains("move-up") && row.previousElementSibling) {
      list.insertBefore(row, row.previousElementSibling);
    } else if (button.classList.contains("move-down") && row.nextElementSibling) {
      list.insertBefore(row.nextElementSibling, row);
    }
    refreshRows();
    if (unsaved) unsaved.hidden = false;
  });

  if (list) {
    Sortable.create(list, {
      animation: 140,
      handle: ".drag-handle",
      onEnd: (event) => {
        refreshRows();
        if (unsaved) unsaved.hidden = false;
        const announcement = document.querySelector<HTMLElement>("#prediction-announcement");
        const row = event.item;
        const team = row.querySelector<HTMLElement>(".prediction-team")?.textContent;
        const position = row.querySelector<HTMLElement>(".prediction-position")?.textContent;
        if (announcement && team && position) {
          announcement.textContent = `${team} moved to position ${position}`;
        }
      },
    });
  }
}
