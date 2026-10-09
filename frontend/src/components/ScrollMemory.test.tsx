import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { BackLink } from './BackLink';
import { ScrollMemory } from './ScrollMemory';

/** jsdom не листает и не знает высоты страницы: позиция, высота и кадры — свои. */
let scrolled = 0;
let tall = 5000;
const frames = new Map<number, FrameRequestCallback>();
let nextFrame = 0;
const scrollTo = vi.fn((_x: number, top: number) => {
  scrolled = top;
});

function flushFrames(): void {
  for (const [id, callback] of [...frames]) {
    frames.delete(id);
    callback(performance.now());
  }
}

/** Человек долистал до `y`: событие прокрутки и кадр, в котором место пишется. */
function scrollBy(y: number): void {
  scrolled = y;
  fireEvent.scroll(window);
  flushFrames();
}

function List() {
  return (
    <>
      <Link to="/card" state={{ from: '?page=2' }}>
        Донор
      </Link>
      <Link to="/list?page=3">Страница 3</Link>
    </>
  );
}

function Card() {
  const navigate = useNavigate();
  return (
    <>
      <BackLink to="/list?page=2">К списку</BackLink>
      <button type="button" onClick={() => void navigate(-1)}>
        Назад
      </button>
    </>
  );
}

function open() {
  renderWith(
    <>
      <ScrollMemory />
      <Routes>
        <Route path="/list" element={<List />} />
        <Route path="/card" element={<Card />} />
      </Routes>
    </>,
    '/list?page=2',
  );
}

beforeEach(() => {
  scrolled = 0;
  tall = 5000;
  frames.clear();
  scrollTo.mockClear();
  sessionStorage.clear();
  vi.stubGlobal('scrollTo', scrollTo);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    nextFrame += 1;
    frames.set(nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  Object.defineProperty(window, 'scrollY', { configurable: true, get: () => scrolled });
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    configurable: true,
    get: () => tall,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('возврат к списку — на то же место', () => {
  it('из низа списка в карточку — карточка сверху; «К списку» — то же место списка', async () => {
    open();
    const user = userEvent.setup();
    scrollBy(1200);

    await user.click(screen.getByRole('link', { name: 'Донор' }));
    expect(scrollTo).toHaveBeenLastCalledWith(0, 0);

    await user.click(screen.getByRole('link', { name: 'К списку' }));
    expect(scrollTo).toHaveBeenLastCalledWith(0, 1200);
  });

  it('«назад» браузера — тоже на место', async () => {
    open();
    const user = userEvent.setup();
    scrollBy(1500);

    await user.click(screen.getByRole('link', { name: 'Донор' }));
    await user.click(screen.getByRole('button', { name: 'Назад' }));

    expect(scrollTo).toHaveBeenLastCalledWith(0, 1500);
  });

  it('другая страница того же списка — прокрутка не трогается', async () => {
    open();
    const user = userEvent.setup();
    scrollBy(900);

    await user.click(screen.getByRole('link', { name: 'Страница 3' }));

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('список ещё дорисовывается — место ставится, когда страница до него доросла', async () => {
    open();
    const user = userEvent.setup();
    scrollBy(1200);
    await user.click(screen.getByRole('link', { name: 'Донор' }));

    tall = 900; // ответ сервера ещё не пришёл — список короткий
    await user.click(screen.getByRole('link', { name: 'К списку' }));
    expect(scrolled).toBeLessThan(1200);

    tall = 5000;
    flushFrames();
    expect(scrollTo).toHaveBeenLastCalledWith(0, 1200);
  });

  it('человек листает сам — возврат не мешает', async () => {
    open();
    const user = userEvent.setup();
    scrollBy(1200);
    await user.click(screen.getByRole('link', { name: 'Донор' }));

    tall = 900;
    await user.click(screen.getByRole('link', { name: 'К списку' }));
    fireEvent.wheel(window);
    tall = 5000;
    flushFrames();

    expect(scrollTo).not.toHaveBeenLastCalledWith(0, 1200);
  });
});
