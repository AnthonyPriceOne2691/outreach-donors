/**
 * Пятна под стеклом.
 *
 * Существуют ровно затем, чтобы размытию было что размывать: поверх
 * ровного градиента стеклянная панель выглядит белой плашкой — это
 * видно на первом же снимке интерфейса. Разметка пустая и не ловит
 * указатель: всё поведение — в `styles/glass.css`.
 */

export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="orb orb1" />
      <div className="orb orb2" />
      <div className="orb orb3" />
      <div className="orb orb4" />
    </div>
  );
}
