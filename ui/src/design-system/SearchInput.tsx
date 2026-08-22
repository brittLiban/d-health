import type { InputHTMLAttributes } from 'react'
import { TextInput } from './TextInput'

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: string
}

export function SearchInput({ label, ...rest }: SearchInputProps) {
  return <TextInput label={label} type="search" {...rest} />
}
