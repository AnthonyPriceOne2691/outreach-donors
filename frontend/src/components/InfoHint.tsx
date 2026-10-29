/**
 * Значок «i» с подсказкой — пояснение, которому не место в строке.
 *
 * Один вид на весь фронт: до 28.09.2026 значок жил только у заголовка
 * вердикта на донорах; пороги получили такие же (замечание 28.09.2026:
 * «добавь значки подсказок, какие значения допустимы»).
 *
 * Подсказка открывается наведением, фокусом с клавиатуры и касанием: на
 * телефоне наведения нет, и подсказка только по наведению была бы с него
 * недоступна. Значок тихий, чтобы не спорить с тем, что поясняет; поверх
 * содержимого подсказка встаёт плотным стеклом (`.mantine-Tooltip-tooltip`).
 *
 * **Подсказка со ссылкой — окошком** (`interactive`, 29.10.2026: формат файла доноров
 * со ссылкой «Скачать шаблон»). Всплывающая подсказка закрывается, стоит указателю
 * сойти со значка, — до ссылки в ней не дотянуться. Окошко открывается наведением и
 * держится, пока указатель на значке или на нём; нажатие или касание его закрепляет и
 * ведёт фокус внутрь — к ссылке; Esc, нажатие мимо и повторное нажатие закрывают.
 * Фокусом окошко не открывается: Esc возвращает фокус на значок и открыл бы его снова.
 *
 * Esc в окошке закрывает только окошко. Окно Mantine (`Modal`) ловит Esc слушателем окна
 * браузера раньше всех обработчиков и закрывается, если у цели нажатия нет признака
 * `data-mantine-stop-propagation`: «i» в окне загрузки закрывало бы и окно с выбранным
 * файлом. Поэтому признак — у значка, пока окошко открыто, и у всего, на чём в окошке
 * бывает фокус, а Esc окошко разбирает само и возвращает фокус на значок.
 */

import { ActionIcon, Popover, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';

interface Props {
  /** Имя значка для программы чтения с экрана — что именно он поясняет. */
  name: string;
  /** Текст подсказки. */
  children: ReactNode;
  /** Ширина подсказки для длинного пояснения — оно переносится в ней по
   *  словам. Без ширины подсказка в одну строку: граница «от 0 до 10 000 000»
   *  в узкой подсказке рвала число пополам. */
  width?: number;
  /** В подсказке ссылка или кнопка — окошком, а не всплывающей подсказкой. */
  interactive?: boolean;
}

/** Вид значка — один у подсказки и у окошка. */
const LOOK = { variant: 'subtle', size: 'sm', radius: 'xl', color: 'gray' } as const;
/** Столько окошко ждёт, пока указатель дойдёт от значка до него. */
const LEAVE_MS = 150;
/** Признак, при котором окно Mantine не закрывается по Esc, и на чём в окошке бывает фокус. */
const KEEP_ESCAPE = 'data-mantine-stop-propagation';
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';

/** Всё фокусируемое в окошке — с признаком: Esc на нём закрывает окошко, а не окно вокруг. */
function keepEscape(node: HTMLDivElement | null): void {
  node?.querySelectorAll(FOCUSABLE).forEach((element) => element.setAttribute(KEEP_ESCAPE, 'true'));
}

export function InfoHint({ name, children, width, interactive = false }: Props) {
  if (interactive) {
    return (
      <InfoCard name={name} width={width}>
        {children}
      </InfoCard>
    );
  }
  return (
    <Tooltip
      multiline={width !== undefined}
      w={width}
      withArrow
      events={{ hover: true, focus: true, touch: true }}
      label={children}
    >
      <ActionIcon {...LOOK} aria-label={name}>
        <IconInfoCircle size={15} />
      </ActionIcon>
    </Tooltip>
  );
}

/** Как открыто окошко: наведением — до ухода указателя, нажатием — до Esc или нажатия мимо. */
type Shown = 'hover' | 'pinned' | null;

function InfoCard({
  name,
  children,
  width,
}: Omit<Props, 'interactive' | 'width'> & {
  width: number | undefined;
}) {
  const [shown, setShown] = useState<Shown>(null);
  const icon = useRef<HTMLButtonElement>(null);
  const leaving = useRef(0);
  useEffect(() => () => window.clearTimeout(leaving.current), []);
  const enter = () => {
    window.clearTimeout(leaving.current);
    setShown((was) => was ?? 'hover');
  };
  const leave = () => {
    window.clearTimeout(leaving.current);
    leaving.current = window.setTimeout(
      () => setShown((was) => (was === 'hover' ? null : was)),
      LEAVE_MS,
    );
  };
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || shown === null) return;
    setShown(null);
    icon.current?.focus();
  };
  return (
    <Popover
      opened={shown !== null}
      onChange={(opened) => {
        if (!opened) setShown(null);
      }}
      width={width}
      position="bottom"
      withArrow
      radius="lg"
      shadow="md"
      trapFocus={shown === 'pinned'}
      closeOnEscape={false}
      middlewares={{ shift: { padding: 16 } }}
    >
      <Popover.Target>
        <ActionIcon
          ref={icon}
          {...LOOK}
          aria-label={name}
          data-mantine-stop-propagation={shown === null ? undefined : 'true'}
          onKeyDown={escape}
          onMouseEnter={enter}
          onMouseLeave={leave}
          onClick={() => setShown((was) => (was === 'pinned' ? null : 'pinned'))}
        >
          <IconInfoCircle size={15} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown
        ref={keepEscape}
        className="glassSolid"
        onKeyDown={escape}
        onMouseEnter={enter}
        onMouseLeave={leave}
      >
        {children}
      </Popover.Dropdown>
    </Popover>
  );
}
