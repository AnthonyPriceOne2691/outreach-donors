/**
 * Отказ шага с кодом — одним куском у второго шага входа и у включения кода (23.11.2026).
 *
 * Отказ сервера показывается его словами; «слишком много попыток» и пауза учётки — это не
 * «неверно», а «подождите», жёлтым. Копии в двух формах разошлись бы на первой правке слов.
 */

import { Alert } from '@mantine/core';
import { useState } from 'react';

import { TooManyAttemptsError, refusalOf } from '../api/client';

export interface CodeRefusal {
  said: string | null;
  waiting: boolean;
  clear: () => void;
  show: (error: unknown) => void;
}

export function useCodeRefusal(): CodeRefusal {
  const [said, setSaid] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  return {
    said,
    waiting,
    clear: () => {
      setSaid(null);
      setWaiting(false);
    },
    show: (error) => {
      setWaiting(error instanceof TooManyAttemptsError);
      setSaid(refusalOf(error));
    },
  };
}

/** Отказ — жёлтым «Подождите» при счётчике попыток, красным со своим заголовком иначе. */
export function CodeRefusalAlert({ refusal, title }: { refusal: CodeRefusal; title: string }) {
  if (refusal.said === null) return null;
  return (
    <Alert color={refusal.waiting ? 'yellow' : 'red'} title={refusal.waiting ? 'Подождите' : title}>
      {refusal.said}
    </Alert>
  );
}
