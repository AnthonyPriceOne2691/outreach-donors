/**
 * Прокрутка колонки меню (`navScroll.ts`, разбор экранов 10.11.2026): у админа 22 пункта, а на
 * 1440 × 900 видно 15 — подсвеченный пункт приезжает в видимую часть при смене адреса,
 * прокруткой самой колонки, а не страницы; у края — тень, пока за ним есть пункты.
 *
 * Раскладки в jsdom нет: колонка здесь — 300 px с верхом на 100, пункт — 40 px, и место пункта
 * зависит от прокрутки колонки, как в браузере. Что в браузере пункт встаёт целиком и не под
 * тенью, замерено на стенде: «Расход» — 765–804 при колонке 91–820 и тени 16 px.
 */

import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { EDGE_ROOM, revealShift, settled } from './navScroll';
import { NavMenu } from './NavMenu';
import { sectionKey, SECTIONS } from './sections';

const TOP = 100;
const HEIGHT = 300;
/** Подпись первой группы над первым пунктом. */
const HEAD = 24;
const ROW = 40;

function rect(top: number, height: number): DOMRect {
  return {
    top,
    bottom: top + height,
    height,
    left: 0,
    right: 200,
    width: 200,
    x: 0,
    y: top,
  } as DOMRect;
}

const box = () => {
  const found = document.querySelector<HTMLElement>('.navScroll');
  if (found === null) throw new Error('Колонки пунктов нет');
  return found;
};

const edges = () => {
  const wrap = box().parentElement;
  return {
    above: wrap?.hasAttribute('data-more-above'),
    below: wrap?.hasAttribute('data-more-below'),
  };
};

beforeEach(() => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.classList.contains('navScroll')) return rect(TOP, HEIGHT);
    const scroller = this.closest<HTMLElement>('.navScroll');
    const links = scroller === null ? [] : [...scroller.querySelectorAll('a')];
    const index = links.indexOf(this as HTMLAnchorElement);
    if (scroller === null || index < 0) return rect(0, 0);
    return rect(TOP + HEAD + index * ROW - scroller.scrollTop, ROW);
  });
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(HEIGHT);
  vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(HEAD + SECTIONS.length * ROW);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Переход по адресу — как по ссылке со страницы, мимо меню. */
function Go() {
  const navigate = useNavigate();
  return (
    <>
      {['/', '/usage', '/journal', '/agent'].map((to) => (
        <button key={to} type="button" onClick={() => void navigate(to)}>
          {`перейти ${to}`}
        </button>
      ))}
    </>
  );
}

function openAt(path: string) {
  const scrollPage = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  const intoView = vi.spyOn(Element.prototype, 'scrollIntoView');
  renderWith(
    <>
      <NavMenu sections={SECTIONS} work={undefined} placeOf={sectionKey} folded={false} />
      <Go />
    </>,
    path,
  );
  return { scrollPage, intoView };
}

/** Где подсвеченный пункт в колонке сейчас. */
function activeRect(): DOMRect {
  const active = box().querySelector('[data-active]');
  if (active === null) throw new Error('Подсвеченного пункта нет');
  return active.getBoundingClientRect();
}

describe('колонка меню: подсвеченный пункт на виду', () => {
  it('смена адреса приводит пункт в видимую часть — прокруткой колонки, а не страницы', async () => {
    const { scrollPage, intoView } = openAt('/');
    expect(box().scrollTop).toBe(0);

    await userEvent.click(screen.getByRole('button', { name: 'перейти /usage' }));

    const shown = activeRect();
    expect(box().scrollTop).toBeGreaterThan(0);
    // Целиком и не под тенью нижнего края.
    expect(shown.top).toBeGreaterThanOrEqual(TOP + EDGE_ROOM);
    expect(shown.bottom).toBeLessThanOrEqual(TOP + HEIGHT - EDGE_ROOM);
    expect(scrollPage).not.toHaveBeenCalled();
    expect(intoView).not.toHaveBeenCalled();
  });

  it('открытый по ссылке раздел — пункт на виду с первого кадра', () => {
    openAt('/journal');

    expect(activeRect().bottom).toBeLessThanOrEqual(TOP + HEIGHT);
  });

  it('пункт, который и так виден, колонку не двигает', async () => {
    openAt('/usage');
    const before = box().scrollTop;

    // «Агент переписки» — пунктом выше «Расхода», в видимой части: колонка стоит, где стояла.
    await userEvent.click(screen.getByRole('button', { name: 'перейти /agent' }));

    expect(before).toBeGreaterThan(0);
    expect(box().scrollTop).toBe(before);
  });

  it('пункт у самого верха — колонка до самого верха, а не на 8 px ниже', async () => {
    openAt('/journal');

    await userEvent.click(screen.getByRole('button', { name: 'перейти /' }));

    expect(box().scrollTop).toBe(0);
    expect(edges().above).toBe(false);
  });
});

describe('колонка меню: тень у края', () => {
  it('пока за краем есть пункты — тень у него, докрученный край — без тени', () => {
    openAt('/');
    expect(edges()).toEqual({ above: false, below: true });

    box().scrollTop = 200;
    fireEvent.scroll(box());
    expect(edges()).toEqual({ above: true, below: true });

    box().scrollTop = HEAD + SECTIONS.length * ROW - HEIGHT;
    fireEvent.scroll(box());
    expect(edges()).toEqual({ above: true, below: false });
  });
});

describe('сдвиг колонки', () => {
  const column = rect(TOP, HEIGHT);

  it('пункт виден — ноль; ниже края — вниз, выше — вверх, с запасом на тень', () => {
    expect(revealShift(column, rect(200, ROW))).toBe(0);
    expect(revealShift(column, rect(380, ROW))).toBe(380 + ROW - (TOP + HEIGHT) + EDGE_ROOM);
    expect(revealShift(column, rect(60, ROW))).toBe(60 - TOP - EDGE_ROOM);
  });

  it('до края меньше двух теней — до самого края; дальше — в пределах прокрутки', () => {
    expect(settled(40, -20, 500)).toBe(0);
    expect(settled(450, 30, 500)).toBe(500);
    expect(settled(200, 50, 500)).toBe(250);
    // Вниз у верха и вверх у низа — без прыжка к краю: пункт ушёл бы под тень.
    expect(settled(0, 20, 500)).toBe(20);
    expect(settled(500, -20, 500)).toBe(480);
  });
});
