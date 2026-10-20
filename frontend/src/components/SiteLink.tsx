/**
 * Имя сайта — ссылкой на сам сайт, в новой вкладке: так читают списки «посмотреть сайт и
 * решить» — очередь рассмотрения и резерв (20.10.2026). Одна ссылка на оба места, а не копия в
 * каждом.
 *
 * Чернилами, как имена в таблицах: бирюзовая ссылка на бирюзовом углу полотна не держит норму
 * контраста (замер 25.09.2026 — 4,30 : 1 при норме 4,5). Длинный домен переносится по швам
 * (`Seams`), а не посреди слова. Размер и отступы задаёт место; цвет, вес и класс — общие, и
 * место их не перебивает: иначе одно имя сайта снова стало бы разным на двух экранах.
 */

import { Anchor } from '@mantine/core';
import type { AnchorProps } from '@mantine/core';

import { Seams } from './Seams';

interface Props extends AnchorProps {
  host: string;
}

export function SiteLink({ host, ...placed }: Props) {
  return (
    <Anchor
      href={`https://${host}`}
      target="_blank"
      rel="noreferrer"
      {...placed}
      c="var(--ink)"
      fw={500}
      underline="hover"
      className="cellName"
    >
      <Seams text={host} />
    </Anchor>
  );
}
