/**
 * Полотно и пятна под стеклом.
 *
 * Пятна существуют ровно затем, чтобы размытию было что размывать:
 * поверх ровного градиента стеклянная панель выглядит белой плашкой.
 *
 * Полотно лежит **двумя слоями** — светлым и тёмным. Градиент в градиент
 * браузер не перетекает, он меняет его рывком; прозрачность перетекает,
 * поэтому смена темы здесь — переливание одного слоя в другой.
 */

export function Backdrop() {
  return (
    <>
      <div className="canvasLayer canvasLight" aria-hidden="true" />
      <div className="canvasLayer canvasDark" aria-hidden="true" />
      <div className="backdrop" aria-hidden="true">
        <div className="orb orb1" />
        <div className="orb orb2" />
        <div className="orb orb3" />
        <div className="orb orb4" />
      </div>
    </>
  );
}
